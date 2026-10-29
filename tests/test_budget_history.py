"""Budget from history (F16): the proposal read from the records, conflicts and flags, and creating the budget."""

from dataclasses import replace
from datetime import date

import pytest

from budget_app.domain.coicop import TAX
from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME
from budget_app.domain.proposal import MEDIAN
from budget_app.services import accounts as acc_svc
from budget_app.services import budget as budget_svc
from budget_app.services import budget_history as hist_svc
from budget_app.services import items as items_svc
from budget_app.services import recurring as rec_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.budget_history import Choice, Options
from budget_app.services.context import ValidationError
from budget_app.services.items import ItemInput
from budget_app.services.recurring import RecurringInput
from budget_app.services.transactions import TransactionInput

TODAY = date(2026, 10, 18)  # basis months end in September 2026; the test plan runs until March 2027


def spend(ctx, setup, day: date, amount: float, category: str = "01.1", currency: str = "CHF", **kw):
    account = setup["eur"].id if currency == "EUR" else setup["chf"].id
    return tx_svc.save(
        ctx,
        TransactionInput(
            date=day,
            type=TX_EXPENDITURE,
            amount=amount,
            currency=currency,
            category=category,
            from_account=account,
            **kw,
        ),
    )


def earn(ctx, setup, day: date, amount: float, category: str | None = None, **kw):
    return tx_svc.save(
        ctx,
        TransactionInput(
            date=day,
            type=TX_INCOME,
            amount=amount,
            currency="CHF",
            category=category or setup["salary"].id,
            to_account=setup["chf"].id,
            **kw,
        ),
    )


def opts(**kw) -> Options:
    return Options(**({"start_month": "2026-10", "step": 0.01} | kw))


def rows(ctx, **kw) -> dict[str, hist_svc.Row]:
    return {r.category: r for r in hist_svc.propose(ctx, opts(**kw), TODAY).rows}


def budget(ctx, category: str, start: str, end: str, amount: float = 100, type_: str = "expenditure", **kw):
    return budget_svc.create(
        ctx,
        BudgetInput(
            start_month=start, end_month=end, type=type_, currency="CHF", amount=amount, category=category, **kw
        ),
    )


# ------------------------------------------------------------------ the history
def test_average_and_median_over_the_basis_with_empty_months_as_zero(ctx, setup):
    for month, amount in ((4, 300), (5, 330), (7, 900), (9, 330)):
        spend(ctx, setup, date(2026, month, 10), amount)
    prop = hist_svc.propose(ctx, opts(), TODAY)
    assert prop.basis == ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
    assert not prop.partial
    [food] = prop.rows
    assert food.history == [300, 330, 0, 900, 0, 330]
    assert food.proposed == 310  # 1860 / 6
    assert rows(ctx, method=MEDIAN)["01.1"].proposed == 315  # (300 + 330) / 2
    assert rows(ctx, basis=3)["01.1"].history == [900, 0, 330]
    assert rows(ctx, basis=3)["01.1"].proposed == 410
    assert rows(ctx, basis=12)["01.1"].history[-6:] == [300, 330, 0, 900, 0, 330]


def test_a_history_that_starts_later_counts_only_its_months(ctx, setup):
    spend(ctx, setup, date(2026, 8, 3), 200)
    spend(ctx, setup, date(2026, 9, 3), 400)
    spend(ctx, setup, date(2026, 10, 3), 9999)  # the current month is not complete: it never counts
    prop = hist_svc.propose(ctx, opts(basis=12), TODAY)
    assert prop.first == "2026-08"
    assert prop.basis == ["2026-08", "2026-09"]
    assert prop.partial and prop.wanted == 12
    assert prop.rows[0].proposed == 300


def test_nothing_to_propose_without_a_complete_month(ctx, setup):
    assert hist_svc.propose(ctx, opts(), TODAY).basis == []  # no records
    spend(ctx, setup, date(2026, 10, 3), 50)
    prop = hist_svc.propose(ctx, opts(), TODAY)
    assert (prop.first, prop.basis, prop.rows, prop.partial) == ("2026-10", [], [], False)


def test_rounding_steps(ctx, setup):
    for month, amount in ((7, 101), (8, 102), (9, 104.5)):
        spend(ctx, setup, date(2026, month, 1), amount)
    assert rows(ctx, basis=3)["01.1"].proposed == 102.5
    assert rows(ctx, basis=3, step=1.0)["01.1"].proposed == 103
    assert rows(ctx, basis=3, step=5.0)["01.1"].proposed == 105
    assert rows(ctx, basis=3, step=10.0)["01.1"].proposed == 100
    assert rows(ctx, basis=3, step=100.0)["01.1"].proposed == 100


def test_levels_fold_the_categories(ctx, setup):
    spend(ctx, setup, date(2026, 9, 1), 60, "01.1.1")
    spend(ctx, setup, date(2026, 9, 2), 30, "01.1.2.1")
    spend(ctx, setup, date(2026, 9, 3), 6, "01.2")
    spend(ctx, setup, date(2026, 9, 4), 12, TAX)
    by_division = rows(ctx, basis=3, level=1)
    assert {c: r.history[-1] for c, r in by_division.items()} == {"01": 96, TAX: 12}
    assert {c: r.history[-1] for c, r in rows(ctx, basis=3, level=2).items()} == {"01.1": 90, "01.2": 6, TAX: 12}
    by_class = rows(ctx, basis=3, level=3)
    assert {c: r.history[-1] for c, r in by_class.items()} == {"01.1.1": 60, "01.1.2": 30, "01.2": 6, TAX: 12}
    assert list(by_class) == ["01.1.1", "01.1.2", "01.2", TAX]  # COICOP order, taxes last


def test_split_lines_count_in_their_own_category(ctx, setup):
    tx = spend(ctx, setup, date(2026, 9, 10), 100, "01.1")
    items_svc.replace(ctx, tx.id, [ItemInput("Bread", 1, 60), ItemInput("Gift", 1, 40, "09.2")])
    found = rows(ctx, basis=3)
    assert (found["01.1"].history[-1], found["09.2"].history[-1]) == (60, 40)


def test_advance_shares_and_repayments_do_not_count(ctx, setup):
    tx = spend(ctx, setup, date(2026, 9, 10), 100, "11.1", is_advance=True, advance_value=50, debtor="Anna")
    earn(ctx, setup, date(2026, 9, 20), 50, is_advance=True, advance_account=tx.advance_account, settles_id=tx.id)
    earn(ctx, setup, date(2026, 9, 25), 6000)
    found = rows(ctx, basis=3)
    assert set(found) == {"11.1", setup["salary"].id}  # no advance row, no repayment row
    assert found["11.1"].history[-1] == 50
    assert found[setup["salary"].id].history[-1] == 6000


def test_amounts_are_in_chf_at_each_transactions_rate(ctx, setup):
    spend(ctx, setup, date(2026, 9, 10), 100, currency="EUR")  # ECB rate of 29 Aug: 0.95 CHF
    ctx.store.settings.display_currency = "EUR"  # the display currency changes nothing
    assert rows(ctx, basis=3)["01.1"].history[-1] == 95


def test_transfers_unknown_and_deleted_categories_are_left_out(ctx, setup):
    savings = acc_svc.add_account(ctx, "Savings", "CHF")
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 1),
            type="transfer",
            amount=500,
            currency="CHF",
            from_account=setup["chf"].id,
            to_account=savings.id,
        ),
    )
    spend(ctx, setup, date(2026, 9, 1), 80, "07.3")
    odd = spend(ctx, setup, date(2026, 9, 2), 20, "07.2")
    ctx.store.transactions.update(replace(odd, category="99.9"))  # a code the app does not know
    earn(ctx, setup, date(2026, 9, 3), 300)
    side = acc_svc.add_income_category(ctx, "Side job")
    earn(ctx, setup, date(2026, 9, 4), 100, category=side.id)
    ctx.store.income_categories.delete([side.id])  # removed behind the app's back
    assert set(rows(ctx, basis=3)) == {"07.3", setup["salary"].id}


# ------------------------------------------------------------------ existing budgets and recurring series
def test_a_budget_in_the_target_months_is_a_conflict(ctx, setup):
    spend(ctx, setup, date(2026, 9, 1), 300)
    spend(ctx, setup, date(2026, 9, 1), 40, "07.3")
    budget(ctx, "01.1", "2026-11", "2027-01", 250)
    budget(ctx, "01.1", "2026-12", "2026-12", 50)  # a second entry in one month adds up
    budget(ctx, "07.3", "2026-04", "2026-09", 30)  # before the target months: no conflict
    found = rows(ctx, basis=3)
    food = found["01.1"]
    assert food.conflict and not food.ticked
    assert (food.existing.months, food.existing.low, food.existing.high) == (3, 250, 300)
    assert found["07.3"].existing is None and found["07.3"].ticked
    # a later start leaves the conflict behind
    assert not rows(ctx, basis=3, start_month="2027-02")["01.1"].conflict


def test_budgets_inside_or_above_the_category_overlap(ctx, setup):
    spend(ctx, setup, date(2026, 9, 1), 300, "01.1.1")
    budget(ctx, "01.1.1", "2026-10", "2026-12", 80)
    budget(ctx, "01", "2027-01", "2027-01", 500)
    budget(ctx, "01.2", "2026-10", "2026-10", 9)  # a sibling: no overlap
    by_group = rows(ctx, basis=3, level=2)["01.1"]
    assert by_group.existing is None and not by_group.ticked
    assert [(o.category, o.months, o.low) for o in by_group.overlaps] == [("01", 1, 500), ("01.1.1", 3, 80)]
    by_class = rows(ctx, basis=3, level=3)["01.1.1"]
    assert by_class.conflict and [o.category for o in by_class.overlaps] == ["01"]


def test_income_budgets_conflict_by_category(ctx, setup):
    earn(ctx, setup, date(2026, 9, 25), 6200)
    budget(ctx, setup["salary"].id, "2026-10", "2027-03", 6000, type_="income")
    budget(ctx, "01.1", "2026-10", "2027-03", 6000)  # expenditure never conflicts with income
    salary = rows(ctx, basis=3)[setup["salary"].id]
    assert salary.conflict and salary.existing.months == 6 and salary.overlaps == []


def test_a_row_that_rounds_to_nothing_starts_unticked(ctx, setup):
    spend(ctx, setup, date(2026, 9, 1), 3)
    row = rows(ctx, basis=3, step=10.0)["01.1"]
    assert row.proposed == 0 and not row.ticked and not row.conflict


def test_recurring_series_are_flagged(ctx, setup):
    spend(ctx, setup, date(2026, 9, 1), 1900, "04.1.1")
    earn(ctx, setup, date(2026, 9, 25), 6200)
    rent = rec_svc.save(
        ctx,
        RecurringInput(
            amount=1900, currency="CHF", category="04.1.1", from_account=setup["chf"].id, start=date(2026, 11, 1)
        ),
    )
    rec_svc.save(  # an ended series is not flagged (its next date lies after its end)
        ctx,
        RecurringInput(
            amount=10,
            currency="CHF",
            category="04.1.2",
            from_account=setup["chf"].id,
            start=date(2026, 1, 1),
            end=date(2026, 2, 1),
        ),
    )
    ended = next(x for x in ctx.store.recurring.all() if x.amount == 10)
    ctx.store.recurring.update(replace(ended, next_due=date(2026, 3, 1)))
    pay = rec_svc.save(
        ctx,
        RecurringInput(
            type="income",
            amount=6200,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
            start=date(2026, 11, 25),
        ),
    )
    by_group = rows(ctx, basis=3, level=2)
    assert [s.id for s in by_group["04.1"].recurring] == [rent.id]
    assert [s.id for s in by_group[setup["salary"].id].recurring] == [pay.id]
    assert [s.id for s in rows(ctx, basis=3, level=1)["04"].recurring] == [rent.id]
    assert by_group["04.1"].ticked  # a flag, not a conflict


def test_an_invalid_period_is_reported_and_the_rows_stay(ctx, setup):
    spend(ctx, setup, date(2026, 9, 1), 300)
    budget(ctx, "01.1", "2026-10", "2026-10", 250)
    prop = hist_svc.propose(ctx, opts(basis=3, start_month="2027-06"), TODAY)
    assert prop.target == []
    assert prop.target_error == "The plan ends in Mar 2027, before the first month. Extend it in Settings."
    assert [r.category for r in prop.rows] == ["01.1"] and not prop.rows[0].conflict
    prop = hist_svc.propose(ctx, opts(basis=3, forever=False, end_month="2026-12"), TODAY)
    assert prop.target == ["2026-10", "2026-11", "2026-12"] and prop.target_error == ""


def test_income_after_expenditure_by_name(ctx, setup):
    bonus = acc_svc.add_income_category(ctx, "a bonus")
    earn(ctx, setup, date(2026, 9, 1), 100, category=bonus.id)
    earn(ctx, setup, date(2026, 9, 2), 100)
    spend(ctx, setup, date(2026, 9, 3), 10, TAX)
    spend(ctx, setup, date(2026, 9, 4), 10, "12.1")
    assert list(rows(ctx, basis=3)) == ["12.1", TAX, bonus.id, setup["salary"].id]


# ------------------------------------------------------------------ creating
def test_create_makes_one_series_per_choice_in_one_undo_step(ctx, setup):
    budget(ctx, "01.1", "2026-10", "2027-03", 250)
    budget(ctx, "01.1", "2026-09", "2026-09", 250)  # before the target months: stays
    before = len(ctx.store.history.actions())
    created = hist_svc.create(
        ctx,
        opts(),
        [
            Choice("expenditure", "01.1", 320, replace=True),
            Choice("expenditure", "07.3", 85.555),
            Choice("income", setup["salary"].id, 6200),
        ],
        "From history: average of Apr 2026 – Sep 2026",
    )
    assert len(created) == 18  # three forever series, October to March
    assert len(ctx.store.history.actions()) == before + 1
    food = sorted((b for b in ctx.store.budget.all() if b.category == "01.1"), key=lambda b: b.month)
    assert [(b.month, b.amount) for b in food] == [("2026-09", 250)] + [
        (m, 320) for m in ("2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03")
    ]
    new = [b for b in ctx.store.budget.all() if b.id in {c.id for c in created}]
    assert all(b.currency == "CHF" and b.forever and b.recur_months == 1 for b in new)
    assert all(b.generated_until == "2027-03" for b in new)
    assert all(b.comment == "From history: average of Apr 2026 – Sep 2026" for b in new)
    assert len({b.series_id for b in new}) == 3
    assert {b.amount for b in new if b.category == "07.3"} == {85.56}

    undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert sorted((b.month, b.amount) for b in ctx.store.budget.all()) == sorted(
        [("2026-09", 250)] + [(m, 250) for m in ("2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03")]
    )


def test_create_without_replace_adds_alongside_and_respects_until(ctx, setup):
    budget(ctx, "01.1", "2026-10", "2026-10", 250)
    created = hist_svc.create(
        ctx, opts(forever=False, end_month="2026-11"), [Choice("expenditure", "01.1", 300)], "note"
    )
    assert [(b.month, b.amount, b.forever) for b in created] == [("2026-10", 300, False), ("2026-11", 300, False)]
    assert sorted(b.amount for b in ctx.store.budget.all() if b.month == "2026-10") == [250, 300]


def test_create_is_all_or_nothing(ctx, setup):
    before = len(ctx.store.history.actions())
    with pytest.raises(ValidationError, match=r"^07\.3 .*: Amount must be a positive number$"):
        hist_svc.create(ctx, opts(), [Choice("expenditure", "01.1", 300), Choice("expenditure", "07.3", 0)])
    with pytest.raises(ValidationError, match=r"^Salary: Amount must be a positive number$"):
        hist_svc.create(ctx, opts(), [Choice("income", setup["salary"].id, None)])
    with pytest.raises(ValidationError, match="before the first month"):
        hist_svc.create(ctx, opts(start_month="2027-06"), [Choice("expenditure", "01.1", 300)])
    with pytest.raises(ValidationError, match="Tick at least one category"):
        hist_svc.create(ctx, opts(), [])
    assert ctx.store.budget.all() == []
    assert len(ctx.store.history.actions()) == before  # nothing written, so no action either


def test_note_and_labels():
    assert hist_svc.note("average", ["2026-04", "2026-05", "2026-09"]) == "From history: average of Apr 2026 – Sep 2026"
    assert hist_svc.note(MEDIAN, ["2026-09"]) == "From history: median of Sep 2026"
    assert hist_svc.note(MEDIAN, []) == "From history: median of "


def test_category_labels(ctx, setup):
    assert hist_svc.category_label(ctx, "income", setup["salary"].id) == "Salary"
    assert hist_svc.category_label(ctx, "income", "gone") == "gone"
    assert hist_svc.category_label(ctx, "expenditure", TAX) == "Taxes"
    assert hist_svc.category_label(ctx, "expenditure", "01.1").startswith("01.1 ")
