"""The Loans page (F22): the Due list (book, skip, book all), the loans with their figures, the details sheet, the
loan sheet (add, edit, delete), and the places elsewhere that know about loan accounts (sidebar badge, Settings,
Budget, Overview)."""

from datetime import date

import pytest
from nicegui import ui
from nicegui.testing import User
from notifications import notified
from test_investments_page import field, settle, sheet_button, sheet_texts, top_sheet
from test_payees_page import click, icon_buttons, newest, put
from test_settings_page import row_button

from budget_app import i18n
from budget_app.domain import months
from budget_app.domain.models import ASSET_LOAN, TX_EXPENDITURE, TX_TRANSFER
from budget_app.services import loans as loan_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.loans import LoanInput
from budget_app.services.transactions import TransactionInput
from budget_app.ui import layout
from budget_app.ui.pages import loans as page

TODAY = date.today()
D = date


def label(d: date) -> str:
    return i18n.date_label(d)


def quarters_ago(n: int) -> date:
    """The last day of the month 3·n months ago."""
    return months.last_day(months.add_months(months.current_month(), -3 * n))


def mortgage(setup, **kw) -> LoanInput:
    base = dict(
        name="Mortgage",
        currency="CHF",
        principal=500_000,
        start=months.first_day(months.add_months(months.current_month(), -12)),
        rate=1.5,
        recur_months=3,
        first_due=quarters_ago(3),
        amortization=2_500,
        cash_account=setup["chf"].id,
        lender="Bank XY",
    )
    return LoanInput(**(base | kw))


@pytest.fixture
def loan(ctx, setup):
    return loan_svc.add(ctx, mortgage(setup))


def texts(user: User) -> list[str]:
    return [lb.text for lb in sorted(user.find(kind=ui.label).elements, key=lambda e: e.id)]


def page_button(user: User, text: str) -> ui.button:
    return newest(user, ui.button, text=text)


# ------------------------------------------------------------------ the page
async def test_the_page_without_loans(user: User, ctx) -> None:
    await user.open("/loans")
    await user.should_see("No loans yet.")
    await user.should_see("0 loans")
    await user.should_see("A mortgage or loan counts against your wealth")  # the empty state explains the page
    await user.should_not_see("Nothing is due.")  # no Due group without a loan
    tabs = [p for p, _ in layout.SECTIONS["/accounts"]]  # a tab of Wealth, after Investments
    assert tabs.index("/loans") == tabs.index("/investments") + 1
    assert "/loans" in layout.BADGES


async def test_loans_with_their_figures_and_details(user: User, ctx, setup, loan) -> None:
    loan_svc.book(ctx, loan.id, quarters_ago(3))
    loan_svc.book(ctx, loan.id, quarters_ago(2), interest=1_870)
    await user.open("/loans")
    await user.should_see("1 loan")
    await user.should_see("495'000.00 CHF outstanding")
    await user.should_see("1.5 % · 2'500.00 CHF every quarter + interest")
    status = next(x for x in texts(user) if x.startswith("next on"))
    assert status.startswith(f"next on {label(quarters_ago(1))} · paid off in ")
    await user.should_see("paid from Bank · Bank XY")
    meters = [e for e in user.find(kind=ui.element).elements if "meter" in e.classes]
    assert meters and "done" not in meters[0].classes
    badge = next(b for b in user.find(kind=ui.badge).elements if b.text == "1")  # one payment due
    assert badge.visible

    with user:
        page.open_loan_details(ctx, loan.id)
    assert "Planned Payments" in sheet_texts(user)
    assert "Booked · 4 transactions" in sheet_texts(user)
    assert "Outstanding" in sheet_texts(user) and f"Interest {TODAY.year}" in sheet_texts(user)
    assert "1'856.25 CHF interest + 2'500.00 CHF repayment" in sheet_texts(user)  # 495'000 × 1.5 % / 4
    assert "492'500.00 CHF left" in sheet_texts(user)
    assert "… and so on" in sheet_texts(user)
    assert f"{label(quarters_ago(2))} · Interest" in sheet_texts(user)
    rows = [e for e in top_sheet(user).descendants() if isinstance(e, ui.element) and "cursor-pointer" in e.classes]
    assert len(rows) == 4
    booked = loan_svc.payments(ctx, loan.id)[0]
    from budget_app.ui.widgets.transaction_form import open_transaction_sheet  # noqa: PLC0415

    with user:
        open_transaction_sheet(ctx, editing=booked)
    assert field(user, ui.number, "Amount").value == booked.amount


async def test_a_paid_off_loan(user: User, ctx, setup) -> None:
    car = loan_svc.add(ctx, mortgage(setup, name="Car", principal=1_000, first_due=quarters_ago(1)))
    tx_svc.save(
        ctx,
        TransactionInput(
            date=quarters_ago(2),  # repaid by hand before the first payment was due
            type=TX_TRANSFER,
            amount=1_000,
            currency="CHF",
            from_account=setup["chf"].id,
            to_account=car.account_id,
        ),
    )
    await user.open("/loans")
    await user.should_see("0.00 CHF outstanding")
    await user.should_see("Paid off")
    await user.should_see("Nothing is due.")
    with user:
        page.open_loan_details(ctx, car.id)
    assert "Nothing is planned: the loan is paid off." in sheet_texts(user)
    assert "No payment booked yet." in sheet_texts(user)
    with user:
        page.open_loan_details(ctx, "gone")
    assert notified(user, "This loan no longer exists")


# ------------------------------------------------------------------ the Due list
async def test_book_skip_and_book_all(user: User, ctx, setup, loan) -> None:
    await user.open("/loans")
    first, second, third = quarters_ago(3), quarters_ago(2), quarters_ago(1)
    await user.should_see(label(first))
    await user.should_see(f"{label(second)} · after the one on {label(first)}")
    await user.should_see("1'865.63 CHF interest + 2'500.00 CHF repayment")  # the second, on the debt after the first
    put(user, newest(user, ui.number, "Interest"), 1_880.4)
    click(user, page_button(user, "Book"))
    assert notified(user, "2 transactions booked")
    booked = loan_svc.payments(ctx, loan.id)
    assert {(tx.type, tx.amount) for tx in booked} == {(TX_TRANSFER, 2_500), (TX_EXPENDITURE, 1_880.4)}
    await user.should_see("497'500.00 CHF outstanding")

    click(user, page_button(user, "Skip"))
    assert notified(user, f"Skipped the payment of {label(second)}")
    assert ctx.store.loans.get(loan.id).next_due == third
    await user.should_not_see("Book All")  # one left

    click(user, page_button(user, "Book"))
    assert notified(user, "2 transactions booked")
    await user.should_see("Nothing is due.")
    assert undo_svc.latest(ctx).label == "Book a loan payment"


async def test_book_all_with_edits_and_a_failure(user: User, ctx, setup, loan) -> None:
    car = loan_svc.add(ctx, mortgage(setup, name="Car", principal=5_000, first_due=quarters_ago(1)))
    broken = ctx.store.loans.get(car.id)
    broken.cash_account = "gone"
    ctx.store.loans.update(broken)
    await user.open("/loans")
    repayments = sorted(
        (n for n in user.find(kind=ui.number).elements if n.props.get("label") == "Repayment"), key=lambda n: n.id
    )
    put(user, repayments[0], 0)  # the mortgage's first payment; the car's row comes after
    click(user, page_button(user, "Book All"))
    await settle()
    assert notified(user, "5 transactions booked")  # 1 + 2 + 2 for the mortgage
    assert notified(user, f"Car · {label(quarters_ago(1))}: Select the From account")  # noqa: S608 - not SQL
    assert loan_svc.debt(ctx, loan) == 495_000
    assert [d.loan.id for d in loan_svc.due(ctx, TODAY)] == [car.id]


async def test_a_stale_row_is_refused(user: User, ctx, setup, loan) -> None:
    await user.open("/loans")
    loan_svc.skip(ctx, loan.id, quarters_ago(3))  # handled elsewhere in the meantime
    click(user, page_button(user, "Book"))
    await settle()
    assert notified(user, "This payment has already been booked or skipped")
    await user.should_see(label(quarters_ago(2)))  # the list caught up
    loan_svc.skip(ctx, loan.id, quarters_ago(2))
    click(user, page_button(user, "Skip"))
    await settle()
    assert user.notify.messages.count("This payment has already been booked or skipped") == 2
    put(user, newest(user, ui.number, "Repayment"), 600_000)
    click(user, page_button(user, "Book"))
    await settle()
    assert notified(user, "exceeds the outstanding debt")
    assert ctx.store.transactions.all() == []


# ------------------------------------------------------------------ the sheet
async def test_add_a_loan_in_the_sheet(user: User, ctx, setup) -> None:
    await user.open("/loans")
    click(user, page_button(user, "New Loan"))
    add = sheet_button(user, "Add Loan")
    assert not add.enabled
    put(user, field(user, ui.input, "Name"), "Mortgage")
    await settle()
    assert "Enter the debt at the start" in sheet_texts(user)
    put(user, field(user, ui.number, "Debt at Start"), 500_000)
    put(user, field(user, ui.number, "Interest Rate"), 1.5)
    put(user, field(user, ui.select, "Repeat"), 3)
    put(user, field(user, ui.input, "First Payment"), D(TODAY.year, 12, 31).isoformat())
    put(user, field(user, ui.number, "Repayment per Payment"), 2_500)
    assert field(user, ui.select, "Paying Account").value == setup["chf"].id  # the only CHF account
    assert field(user, ui.select, "Interest Category").value == "12.2.1"
    put(user, field(user, ui.input, "Lender"), "Bank XY")
    await settle()
    assert add.enabled
    preview = next(x for x in sheet_texts(user).split(" | ") if x.startswith("First payment on"))
    assert preview.startswith(
        f"First payment on {label(D(TODAY.year, 12, 31))}: 1'875.00 CHF interest + 2'500.00 CHF repayment · paid off in"
    )
    assert "of interest" in preview
    put(user, field(user, ui.number, "Repayment per Payment"), None)
    await settle()
    assert "· interest only" in sheet_texts(user)
    put(user, field(user, ui.number, "Debt at Start"), 0)
    await settle()
    assert "No debt yet: the schedule starts once the payout is recorded." in sheet_texts(user)
    put(user, field(user, ui.number, "Debt at Start"), 500_000)
    click(user, sheet_button(user, "Add Loan"))
    assert notified(user, "Added Mortgage")
    [saved] = ctx.store.loans.all()
    acc = ctx.store.accounts.get(saved.account_id)
    assert (acc.name, acc.asset_class, saved.rate, saved.amortization, saved.lender) == (
        "Mortgage",
        ASSET_LOAN,
        1.5,
        0,
        "Bank XY",
    )
    await user.should_see("500'000.00 CHF outstanding")
    await user.should_see("1.5 % · interest only, every quarter")


async def test_edit_a_loan(user: User, ctx, setup, loan) -> None:
    loan_svc.book(ctx, loan.id, quarters_ago(3))
    await user.open("/loans")
    click(user, icon_buttons(user, "o_edit")[0])
    assert "Edit Loan" in sheet_texts(user)
    assert "Payments already booked stay as they are" in sheet_texts(user)
    assert "The currency can’t change while transactions use this loan’s account." in sheet_texts(user)
    assert field(user, ui.number, "Interest Rate").value == 1.5
    put(user, field(user, ui.number, "Interest Rate"), 1.25)
    put(user, field(user, ui.input, "Fixed Until"), D(TODAY.year + 2, 3, 31).isoformat())
    click(user, sheet_button(user, "Save Changes"))
    assert notified(user, "Changes saved")
    fresh = ctx.store.loans.get(loan.id)
    assert (fresh.rate, fresh.fixed_until) == (1.25, D(TODAY.year + 2, 3, 31).isoformat())
    await user.should_see(f"fixed until {label(D(TODAY.year + 2, 3, 31))}")
    click(user, icon_buttons(user, "o_edit")[0])
    put(user, field(user, ui.input, "Name"), "Bank")
    await settle()
    assert not sheet_button(user, "Save Changes").enabled
    assert "An account 'Bank' in CHF already exists" in sheet_texts(user)


async def test_delete_a_loan(user: User, ctx, setup, loan) -> None:
    loan_svc.book(ctx, loan.id, quarters_ago(3))
    await user.open("/loans")
    click(user, icon_buttons(user, "o_delete")[0])
    await user.should_see("Delete “Mortgage”?")
    click(user, newest(user, ui.button, text="Delete"))
    assert notified(user, "This loan has transactions")
    tx_svc.delete(ctx, [tx.id for tx in ctx.store.transactions.all()])
    click(user, icon_buttons(user, "o_delete")[0])
    click(user, newest(user, ui.button, text="Delete"))
    assert notified(user, "Deleted Mortgage")
    assert ctx.store.loans.all() == [] and ctx.store.accounts.get(loan.account_id) is None
    await user.should_see("0 loans")


async def test_a_change_elsewhere_refreshes_the_page(user: User, ctx, setup) -> None:
    await user.open("/loans")
    loan_svc.add(ctx, mortgage(setup))
    with user:
        layout.data_changed()
    await user.should_see("1 loan")


# ------------------------------------------------------------------ elsewhere
async def test_accounts_budget_and_overview_know_loan_accounts(user: User, ctx, setup, loan) -> None:
    prev = months.previous_month()
    wealth_svc.upsert_entry(ctx, setup["chf"].id, prev, 20_000)
    await user.open("/accounts")
    await user.should_see("Loans and mortgages · 500'000.00 CHF outstanding")
    row_button(user, "Mortgage", "o_edit").click()
    await settle()
    sheet = top_sheet(user)
    select = next(e for e in sheet.descendants() if isinstance(e, ui.select) and e.props.get("label") == "Asset class")
    assert select.value == ASSET_LOAN and not select.enabled and list(select.options) == [ASSET_LOAN]
    assert "The outstanding debt of its loan on the Loans page" in sheet_texts(user)
    click(user, sheet_button(user, "Cancel"))
    await user.open("/accounts")
    click(user, page_button(user, "Add Account…"))
    select = field(user, ui.select, "Asset class")
    assert ASSET_LOAN not in select.options

    await user.open("/budget")
    cm = months.current_month()
    [stepper] = [s for s in user.find(kind=ui.select).elements if cm in (s.options or {}) and not s.props.get("label")]
    put(user, stepper, prev)
    await user.should_see("Outstanding debt of the loan")
    await user.should_see("−480'000.00 CHF")  # wealth: 20'000 − 500'000
    click(user, page_button(user, "Record Balance"))
    assert loan.account_id not in field(user, ui.select, "Account").options

    await user.open("/")
    await user.should_see("By Asset Class")
    await user.should_see("Loans and mortgages")
    await user.should_see("−500'000.00 CHF")
    await user.should_see("20'000.00 CHF · 100 %")
