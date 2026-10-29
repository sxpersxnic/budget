"""*Propose from History…* on the Budget Plan page (F16): the sheet, its inputs, conflicts, flags and creating."""

from datetime import date

from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified, undo_toasts

from budget_app.domain import months
from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME
from budget_app.services import budget as budget_svc
from budget_app.services import recurring as rec_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.budget_history import Budgeted
from budget_app.services.recurring import RecurringInput
from budget_app.services.transactions import TransactionInput
from budget_app.ui.pages.budget_proposal import amount_range, whole


# ------------------------------------------------------------------ helpers
def sheet(user: User) -> ui.dialog:
    """The newest open sheet (closed ones stay in the simulation)."""
    with user:
        found = sorted(
            (e for e in user.client.elements.values() if isinstance(e, ui.dialog) and e.value), key=lambda d: d.id
        )
    assert found, "expected an open sheet"
    return found[-1]


def is_open(user: User, dlg: ui.dialog) -> bool:
    with user:
        return bool(dlg.value)


async def open_sheet(user: User) -> ui.dialog:
    await user.open("/budget-entries")
    with user:  # an item of the page's More menu (closed menus count as hidden in the simulation)
        item = next(
            e
            for e in user.client.elements.values()
            if isinstance(e, ui.menu_item)
            and any(getattr(c, "text", "") == "Propose from History…" for c in e.descendants())
        )
    UserInteraction(user, {item}, None).click()
    dlg = sheet(user)
    assert "Propose from History" in texts(dlg)
    return dlg


def texts(root: ui.element) -> list[str]:
    return [e.text for e in root.descendants() if isinstance(e, ui.label)]


def field(root: ui.element, kind: type, label: str | None = None):
    found = sorted(
        (e for e in root.descendants() if isinstance(e, kind) and (label is None or e.props.get("label") == label)),
        key=lambda e: e.id,
    )
    assert found, f"no {kind.__name__} {label!r}"
    return found[0]


def set_value(user: User, element, value) -> None:
    with user:
        element.value = value


def row_elements(dlg: ui.dialog) -> dict[str, ui.element]:
    """The rows of the table by key ("expenditure:01.1", "income:<id>")."""
    return {e.props["data-key"]: e for e in dlg.descendants() if "data-key" in e.props}


def row(dlg: ui.dialog, key: str) -> tuple[ui.checkbox, ui.number, list[str]]:
    el = row_elements(dlg)[key]
    return field(el, ui.checkbox), field(el, ui.number), texts(el)


def tick(user: User, dlg: ui.dialog, key: str) -> None:
    UserInteraction(user, {row(dlg, key)[0]}, None).click()


def create_button(dlg: ui.dialog) -> ui.button:
    return next(e for e in dlg.descendants() if isinstance(e, ui.button) and e.text.startswith("Create Budget"))


def status(dlg: ui.dialog) -> str:
    return next(e.text for e in dlg.descendants() if isinstance(e, ui.label) and e.classes[:2] == ["caption", "flex-1"])


def spend(ctx, setup, day: date, amount: float, category: str, **kw) -> None:
    tx_svc.save(
        ctx,
        TransactionInput(
            date=day,
            type=TX_EXPENDITURE,
            amount=amount,
            currency="CHF",
            category=category,
            from_account=setup["chf"].id,
            **kw,
        ),
    )


def earn(ctx, setup, day: date, amount: float) -> None:
    tx_svc.save(
        ctx,
        TransactionInput(
            date=day,
            type=TX_INCOME,
            amount=amount,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
        ),
    )


def ago(k: int, day: int = 10) -> date:
    return months.first_day(months.add_months(months.current_month(), -k)).replace(day=day)


def history(ctx, setup) -> None:
    """Six months of groceries (one month with nothing), bus tickets and pay; no budgets."""
    for k, food in ((6, 300), (5, 330), (4, 0), (3, 900), (2, 0), (1, 330)):
        if food:
            spend(ctx, setup, ago(k), food, "01.1.1")
        spend(ctx, setup, ago(k, 12), 85, "07.3.2")
        earn(ctx, setup, ago(k, 25), 6200)


# ------------------------------------------------------------------ the proposal
async def test_proposes_from_the_last_six_months(user: User, ctx, setup) -> None:
    history(ctx, setup)
    dlg = await open_sheet(user)
    cm = months.current_month()
    span = f"{months.label(months.add_months(cm, -6))} – {months.label(months.add_months(cm, -1))}"
    assert f"Based on 6 complete months: {span}. Amounts in CHF; a month without spending counts as 0." in texts(dlg)
    assert list(row_elements(dlg)) == ["expenditure:01.1", "expenditure:07.3", f"income:{setup['salary'].id}"]
    box, amount, lines = row(dlg, "expenditure:01.1")
    assert box.text.startswith("01.1 ") and box.value
    assert amount.value == 310  # 1'860 / 6, to 10 CHF
    assert "300 · 330 · 0 · 900 · 0 · 330" in lines
    assert "–" in lines  # no budget yet
    assert row(dlg, "expenditure:07.3")[1].value == 90  # 85 rounded to 10 CHF (half up)
    assert row(dlg, f"income:{setup['salary'].id}")[1].value == 6200
    assert "Expenditure" in texts(dlg) and "Income" in texts(dlg)
    assert create_button(dlg).text == "Create Budget for 3 Categories" and create_button(dlg).enabled
    assert status(dlg) == (
        f"400.00 CHF spending and 6'200.00 CHF income a month from {months.label(cm)}, continued as the plan grows"
    )


async def test_inputs_recalculate(user: User, ctx, setup) -> None:
    history(ctx, setup)
    dlg = await open_sheet(user)
    basis, method = sorted((e for e in dlg.descendants() if isinstance(e, ui.toggle)), key=lambda e: e.id)
    set_value(user, method, "median")
    assert row(dlg, "expenditure:01.1")[1].value == 320  # (300 + 330) / 2 = 315, half up to 10
    set_value(user, field(dlg, ui.select, "Round to"), 1.0)
    assert row(dlg, "expenditure:01.1")[1].value == 315
    set_value(user, basis, 3)
    assert "900 · 0 · 330" in row(dlg, "expenditure:01.1")[2]
    assert row(dlg, "expenditure:01.1")[1].value == 330
    set_value(user, field(dlg, ui.select, "Level"), 3)
    assert list(row_elements(dlg))[:2] == ["expenditure:01.1.1", "expenditure:07.3.2"]
    set_value(user, field(dlg, ui.select, "Level"), 1)
    assert list(row_elements(dlg))[:2] == ["expenditure:01", "expenditure:07"]
    set_value(user, field(dlg, ui.select, "Round to"), 0.01)
    set_value(user, basis, 12)
    assert row(dlg, "expenditure:01")[1].value == 315  # only six months of records: the median of six


async def test_a_typed_amount_survives_a_period_change_but_not_a_new_calculation(user: User, ctx, setup) -> None:
    history(ctx, setup)
    dlg = await open_sheet(user)
    cm = months.current_month()
    set_value(user, row(dlg, "expenditure:01.1")[1], 450)
    assert status(dlg).startswith("540.00 CHF spending")
    set_value(user, field(dlg, ui.select, "Until"), months.add_months(cm, 2))
    assert row(dlg, "expenditure:01.1")[1].value == 450
    span = f"{months.label(cm)} – {months.label(months.add_months(cm, 2))}"
    assert status(dlg) == f"540.00 CHF spending and 6'200.00 CHF income a month, {span}"
    set_value(user, field(dlg, ui.select, "From"), months.add_months(cm, 3))
    assert field(dlg, ui.select, "Until").value is None  # the end lay before the new start
    assert status(dlg) == "Select the final month"
    assert not create_button(dlg).enabled
    set_value(user, field(dlg, ui.select, "Until"), "forever")
    assert row(dlg, "expenditure:01.1")[1].value == 450
    set_value(user, sorted((e for e in dlg.descendants() if isinstance(e, ui.toggle)), key=lambda e: e.id)[1], "median")
    assert row(dlg, "expenditure:01.1")[1].value == 320


async def test_ticked_rows_need_an_amount(user: User, ctx, setup) -> None:
    history(ctx, setup)
    dlg = await open_sheet(user)
    set_value(user, row(dlg, "expenditure:07.3")[1], 0)
    assert status(dlg) == "Every ticked category needs an amount above 0."
    assert not create_button(dlg).enabled
    tick(user, dlg, "expenditure:07.3")  # untick it
    assert create_button(dlg).enabled
    assert create_button(dlg).text == "Create Budget for 2 Categories"
    tick(user, dlg, "expenditure:01.1")
    tick(user, dlg, f"income:{setup['salary'].id}")
    assert create_button(dlg).text == "Create Budget for 0 Categories" and not create_button(dlg).enabled


async def test_creates_the_ticked_rows_in_one_undo_step(user: User, ctx, setup) -> None:
    history(ctx, setup)
    dlg = await open_sheet(user)
    set_value(user, field(dlg, ui.select, "Until"), months.add_months(months.current_month(), 1))
    set_value(user, row(dlg, "expenditure:01.1")[1], 333.333)
    tick(user, dlg, "expenditure:07.3")
    UserInteraction(user, {create_button(dlg)}, None).click()
    await user.should_see("Created 4 budget entries")
    assert notified(user, "Created 4 budget entries")
    assert any(n.props["options"].get("actions") for n in undo_toasts(user))  # with Undo
    assert not is_open(user, dlg)
    entries = ctx.store.budget.all()
    assert sorted((b.category, b.amount) for b in entries) == sorted(
        [("01.1", 333.33)] * 2 + [(setup["salary"].id, 6200)] * 2
    )
    assert all(b.comment.startswith("From history: average of ") and b.currency == "CHF" for b in entries)
    await user.should_see("4 budget entries")  # the list behind the sheet refreshed
    action = ctx.store.history.actions(limit=1)[0]
    assert action.label == "Add budget entries from history" and len(action.changes) == 4


# ------------------------------------------------------------------ conflicts and flags
async def test_conflicts_start_unticked_and_replace_when_ticked(user: User, ctx, demo) -> None:
    dlg = await open_sheet(user)
    keys = list(row_elements(dlg))
    assert keys == ["expenditure:01.1", "expenditure:04.1", "expenditure:07.3", f"income:{demo['salary'].id}"]
    box, amount, lines = row(dlg, "expenditure:01.1")
    assert not box.value and amount.value == 640
    n = len(months.month_range(months.current_month(), ctx.horizon()))
    assert f"Has a budget in {n} of these months. Tick to replace it." in lines
    assert "700.00 CHF" in lines
    assert create_button(dlg).text == "Create Budget for 0 Categories"
    tick(user, dlg, "expenditure:01.1")
    assert status(dlg).endswith("continued as the plan grows · replaces 1 budget")
    UserInteraction(user, {create_button(dlg)}, None).click()
    await user.should_see(f"Created {n} budget entries")
    food = [b for b in ctx.store.budget.all() if b.category == "01.1" and b.month >= months.current_month()]
    assert sorted({b.amount for b in food}) == [640] and len(food) == n
    past = [b for b in ctx.store.budget.all() if b.category == "01.1" and b.month < months.current_month()]
    assert {b.amount for b in past} == {700}  # months before the start keep their budget


async def test_overlaps_and_recurring_series_are_flagged(user: User, ctx, setup) -> None:
    history(ctx, setup)
    cm = months.current_month()
    budget_svc.create(ctx, BudgetInput(start_month=cm, end_month=cm, currency="CHF", amount=120, category="01.1.1"))
    rec_svc.save(
        ctx,
        RecurringInput(
            amount=85,
            currency="CHF",
            category="07.3.2",
            from_account=setup["chf"].id,
            start=months.first_day(months.add_months(cm, 1)),
            payee="SBB",
        ),
    )
    dlg = await open_sheet(user)
    box, _, lines = row(dlg, "expenditure:01.1")
    assert not box.value
    assert any(
        s.startswith("Overlaps the budget for 01.1.1 ") and s.endswith("(120.00 CHF); both would count.") for s in lines
    )
    box, _, lines = row(dlg, "expenditure:07.3")
    assert box.value
    assert any(
        s.startswith("Booked by a recurring transaction: SBB · 07.3.2 ") and s.endswith(", 85.00 CHF (Every month)")
        for s in lines
    )


async def test_a_plan_that_has_ended_is_explained(user: User, ctx, setup) -> None:
    history(ctx, setup)
    past = months.add_months(months.current_month(), -2)
    ctx.store.settings.budget_horizon = past
    dlg = await open_sheet(user)
    assert status(dlg) == f"The plan ends in {months.label(past)}, before the first month. Extend it in Settings."
    assert not create_button(dlg).enabled
    assert row_elements(dlg)  # the proposal is still shown


async def test_a_failure_when_saving_is_shown(user: User, ctx, setup) -> None:
    history(ctx, setup)
    dlg = await open_sheet(user)
    past = months.add_months(months.current_month(), -2)
    ctx.store.settings.budget_horizon = past  # shortened elsewhere while the sheet was open
    UserInteraction(user, {create_button(dlg)}, None).click()
    await user.should_see(f"The plan ends in {months.label(past)}, before the first month. Extend it in Settings.")
    assert is_open(user, dlg)
    assert ctx.store.budget.all() == []


async def test_cancel_creates_nothing(user: User, ctx, setup) -> None:
    history(ctx, setup)
    dlg = await open_sheet(user)
    UserInteraction(
        user, {next(e for e in dlg.descendants() if isinstance(e, ui.button) and e.text == "Cancel")}, None
    ).click()
    assert not is_open(user, dlg)
    assert ctx.store.budget.all() == []


# ------------------------------------------------------------------ short and empty histories
async def test_a_history_that_starts_later(user: User, ctx, setup) -> None:
    spend(ctx, setup, ago(2), 200, "01.1")
    spend(ctx, setup, ago(1), 400, "01.1")
    dlg = await open_sheet(user)
    first, last = months.add_months(months.current_month(), -2), months.add_months(months.current_month(), -1)
    assert (
        f"Your records start in {months.label(first)}, so 2 months count: "
        f"{months.label(first)} – {months.label(last)}. Amounts in CHF." in texts(dlg)
    )
    assert row(dlg, "expenditure:01.1")[1].value == 300


async def test_one_month_of_records(user: User, ctx, setup) -> None:
    spend(ctx, setup, ago(1), 50, "01.1")
    dlg = await open_sheet(user)
    month = months.label(months.add_months(months.current_month(), -1))
    assert f"Your records start in {month}, so 1 month counts: {month}. Amounts in CHF." in texts(dlg)
    basis = sorted((e for e in dlg.descendants() if isinstance(e, ui.toggle)), key=lambda e: e.id)[0]
    set_value(user, basis, 3)
    set_value(user, basis, 6)
    assert row(dlg, "expenditure:01.1")[1].value == 50


async def test_nothing_to_propose(user: User, ctx, setup) -> None:
    dlg = await open_sheet(user)
    assert "There is no complete month with records yet, so there is nothing to propose." in texts(dlg)
    assert row_elements(dlg) == {}
    assert not create_button(dlg).enabled


async def test_months_with_only_advances(user: User, ctx, setup) -> None:
    spend(ctx, setup, ago(1), 80, "11.1", is_advance=True, advance_value=100, debtor="Anna")
    dlg = await open_sheet(user)
    assert "Nothing was spent or received in these months." in texts(dlg)


def test_whole_francs() -> None:
    assert [whole(v) for v in (0, 12.5, 1900, 1234567.4, -12)] == ["0", "12", "1'900", "1'234'567", "−12"]


def test_budget_ranges() -> None:
    assert amount_range(Budgeted("01.1", 3, 250, 250)) == "250.00 CHF"
    assert amount_range(Budgeted("01.1", 3, 250, 300)) == "250.00 – 300.00 CHF"
