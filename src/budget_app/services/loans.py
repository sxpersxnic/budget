"""Loans and mortgages (F22): a loan is a liability account with a repayment schedule.

- The account (asset class `loan`) is added with the loan and owes `principal` on `start`. Its balance is minus the
  **outstanding debt** (`debt`): the principal plus what left the account (a further drawdown), minus what came in
  (repayments). It is computed, never recorded (`value_entry`, used by `wealth.balance_entry` like a securities
  account's), so the loan's month reconciles by construction. A loan paid into one of your accounts starts with a
  principal of 0 and a transfer from the loan account.
- **Payments** fall due every `recur_months` months from `first_due` on; they are proposed with the interest on the
  debt before the due date (`domain.loans.interest`) and the amortization, and **booked** from the Loans page like
  recurring transactions (F05, decision 14): a transfer of the amortization from the paying account into the loan
  account and an expenditure of the interest in the loan's interest category (`INTEREST_CATEGORY`, which the tax
  summary counts as debt interest). Both are ordinary transactions carrying `loan_id`, editable and deletable like
  any other; booking or skipping advances `next_due`.
- The **projection** is untouched: amortization moves money between two accounts, and the interest is budgeted like
  any other cost (a budget beside the loan counts once, as with recurring transactions).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from budget_app.config import BASE_CURRENCY
from budget_app.domain import loans as plan
from budget_app.domain import months
from budget_app.domain.coicop import TAX, get_coicop
from budget_app.domain.fx import FxError
from budget_app.domain.loans import INTEREST_CATEGORY, Payment
from budget_app.domain.models import (
    ACCOUNT_REGULAR,
    TX_EXCHANGE,
    TX_EXPENDITURE,
    TX_TRANSFER,
    Account,
    AccountEntry,
    Loan,
    Transaction,
)
from budget_app.i18n import N_, t
from budget_app.services import accounts as acc_svc
from budget_app.services import payees as payee_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.recurring import next_after, previous_before
from budget_app.services.transactions import TransactionInput
from budget_app.services.undo import recorded

EPS = plan.EPS
RENEWAL_DAYS = 90  # a fixed term ending within this many days is pointed out


@dataclass
class LoanInput:
    name: str = ""
    currency: str = ""
    principal: float | None = None  # the debt on `start`; 0 when the payout is booked as a transfer
    start: date | None = None
    rate: float | None = None  # percent a year
    recur_months: int = 1
    first_due: date | None = None
    amortization: float | None = None  # per payment; none or 0: interest only
    cash_account: str = ""
    interest_category: str = INTEREST_CATEGORY
    lender: str = ""
    fixed_until: date | None = None
    comment: str = ""


@dataclass
class DuePayment:
    """A payment due and not yet booked or skipped, with the amounts the schedule proposes."""

    loan: Loan
    date: date
    interest: float
    amortization: float
    first: bool = True  # the loan's earliest due payment: the one to book or skip next


@dataclass
class Booking:
    booked: list[Transaction] = field(default_factory=list)
    failed: list[tuple[DuePayment, str]] = field(default_factory=list)


# changes made in the Due list before booking: (loan id, due date) -> (interest, amortization, booking date)
Changes = dict[tuple[str, date], tuple[float | None, float | None, date | None]]


@dataclass
class LoanStatus:
    loan: Loan
    account: Account
    debt: float  # outstanding today, in the account's currency
    upcoming: list[Payment]  # the next payments as planned, from `next_due`
    payoff: date | None  # when the schedule clears the debt; None for an interest-only loan
    interest_year: float  # interest booked this calendar year

    @property
    def paid_off(self) -> bool:
        return self.debt <= EPS

    @property
    def repaid(self) -> float:
        return round(max(self.loan.principal - self.debt, 0.0), 2)

    @property
    def share(self) -> float:
        return plan.repaid_share(self.loan.principal, self.debt)

    @property
    def next(self) -> Payment | None:
        return self.upcoming[0] if self.upcoming else None


# ------------------------------------------------------------------ lookups
def _today(today: date | None) -> date:
    return today or date.today()


def loans(ctx: Context) -> list[Loan]:
    """Every loan, by the name of its account."""
    names = {a.id: a.name.lower() for a in ctx.store.accounts.all()}
    return sorted(ctx.store.loans.all(), key=lambda ln: (names.get(ln.account_id, ""), ln.id))


def get(ctx: Context, loan_id: str) -> Loan:
    loan = ctx.store.loans.get(loan_id)
    if loan is None:
        raise ValidationError(t("This loan no longer exists"))
    return loan


def of_account(ctx: Context, account_id: str) -> Loan | None:
    return next((ln for ln in ctx.store.loans.all() if ln.account_id == account_id), None)


def account_of(ctx: Context, loan: Loan) -> Account:
    acc = ctx.store.accounts.get(loan.account_id)
    if acc is None:
        raise ValidationError(t("Account not found"))
    return acc


def title(ctx: Context, loan: Loan) -> str:
    acc = ctx.store.accounts.get(loan.account_id)
    return acc.name if acc else ""


# ------------------------------------------------------------------ the debt
def debt(ctx: Context, loan: Loan, on: date | None = None) -> float:
    """The outstanding debt after the transactions dated up to and including `on` (today by default), positive:
    the principal, plus what left the loan account, minus what came in."""
    day = _today(on)
    total = loan.principal
    for tx in ctx.store.transactions.all():
        if tx.date > day:
            continue
        if tx.from_account == loan.account_id:
            total += tx.amount
        if tx.to_account == loan.account_id:
            total -= tx.to_amount if tx.type == TX_EXCHANGE else tx.amount
    return round(total, 2)


def value_entry(ctx: Context, acc: Account, month: str) -> AccountEntry | None:
    """The month-end balance of a loan account, computed (not stored): minus the debt at the end of `month`; None
    before the month the loan started, and for a loan account without a loan."""
    loan = of_account(ctx, acc.id)
    if loan is None or months.month_of(loan.start) > month:
        return None
    rate_date = ctx.month_rate_date(month)
    amount = round(-debt(ctx, loan, months.last_day(month)), 2) + 0.0
    return AccountEntry(
        account_id=acc.id,
        month=month,
        amount=amount,
        currency=acc.currency,
        amount_chf=round(ctx.convert_on(amount, acc.currency, BASE_CURRENCY, rate_date), 6),
        fx_date=rate_date,
        id=f"loan:{acc.id}:{month}",
    )


def first_month(ctx: Context) -> str | None:
    """The month the earliest loan started."""
    starts = [ln.start for ln in ctx.store.loans.all()]
    return months.month_of(min(starts)) if starts else None


# ------------------------------------------------------------------ status and schedule
def payments(ctx: Context, loan_id: str) -> list[Transaction]:
    """The transactions booked for the loan, newest first."""
    rows = [tx for tx in ctx.store.transactions.all() if tx.loan_id == loan_id]
    return sorted(rows, key=lambda tx: (tx.date, tx.id), reverse=True)


def interest_paid(ctx: Context, loan_id: str, year: int) -> float:
    """The interest booked for the loan in `year`, in the loan's currency."""
    total = sum(tx.amount for tx in payments(ctx, loan_id) if tx.type == TX_EXPENDITURE and tx.date.year == year)
    return round(total, 2)


def status(ctx: Context, loan: Loan, today: date | None = None) -> LoanStatus:
    day = _today(today)
    acc = account_of(ctx, loan)
    owed = debt(ctx, loan, day)
    full = plan.schedule(owed, loan.rate, loan.recur_months, loan.amortization, loan.next_due)
    upcoming = full[: plan.PREVIEW]
    return LoanStatus(loan, acc, owed, upcoming, plan.payoff(full), interest_paid(ctx, loan.id, day.year))


def statuses(ctx: Context, today: date | None = None) -> list[LoanStatus]:
    return [status(ctx, ln, today) for ln in loans(ctx)]


def renewals(ctx: Context, today: date | None = None, within: int = RENEWAL_DAYS) -> list[Loan]:
    """Loans whose fixed term ends within `within` days (or has ended), by that date."""
    day = _today(today)
    found = [ln for ln in loans(ctx) if (end := ln.fixed_until_date) is not None and end <= day + timedelta(within)]
    return sorted(found, key=lambda ln: ln.fixed_until)


# ------------------------------------------------------------------ add, change, delete
def validate(ctx: Context, inp: LoanInput, editing_id: str = "") -> None:
    loan = ctx.store.loans.get(editing_id) if editing_id else None
    acc_svc.check_name(ctx, inp.name, inp.currency, ACCOUNT_REGULAR, exclude_id=loan.account_id if loan else "")
    if inp.principal is None or inp.principal < 0:
        raise ValidationError(t("Enter the debt at the start (0 when the payout is booked as a transfer)"))
    if inp.start is None:
        raise ValidationError(t("Select the start date"))
    if inp.rate is None or inp.rate < 0:
        raise ValidationError(t("Enter the interest rate (0 for an interest-free loan)"))
    if inp.recur_months < 1:
        raise ValidationError(t("The recurrence must be at least every month"))
    if inp.first_due is None:
        raise ValidationError(t("Select the first payment date"))
    if inp.first_due < inp.start:
        raise ValidationError(t("The first payment must not be before the start"))
    if (inp.amortization or 0) < 0:
        raise ValidationError(t("The repayment per payment can’t be negative"))
    cash = ctx.store.accounts.get(inp.cash_account) if inp.cash_account else None
    if cash is None or cash.kind != ACCOUNT_REGULAR or cash.holds_securities or cash.is_loan:
        raise ValidationError(t("Select the paying account"))
    if cash.currency != inp.currency:
        raise ValidationError(t("The paying account must be in {currency}", currency=inp.currency))
    if inp.interest_category != TAX and get_coicop().get(inp.interest_category) is None:
        raise ValidationError(t("Select the interest category"))
    if inp.fixed_until is not None and inp.fixed_until < inp.start:
        raise ValidationError(t("Fixed until must not be before the start"))


def _apply(ctx: Context, loan: Loan, inp: LoanInput) -> Loan:
    """Copy the checked input onto the loan (its account and schedule position are handled by the caller)."""
    if inp.start is None or inp.first_due is None:  # pragma: no cover - checked by validate() first
        raise ValidationError(t("Select the start date"))
    loan.principal = round(float(inp.principal or 0), 2)
    loan.start = inp.start
    loan.rate = round(float(inp.rate or 0), 4)
    loan.first_due = inp.first_due
    loan.recur_months = int(inp.recur_months)
    loan.amortization = round(float(inp.amortization or 0), 2)
    loan.cash_account = inp.cash_account
    loan.interest_category = inp.interest_category
    loan.lender = payee_svc.spelling(ctx, inp.lender)  # a payee once a payment is booked
    loan.fixed_until = inp.fixed_until.isoformat() if inp.fixed_until else ""
    loan.comment = (inp.comment or "").strip()
    return loan


@recorded(N_("Add a loan"))
def add(ctx: Context, inp: LoanInput) -> Loan:
    """A new loan with its liability account, in one undoable step."""
    validate(ctx, inp)
    acc = acc_svc.add_loan_account(ctx, inp.name, inp.currency)
    if inp.start is None or inp.first_due is None:  # pragma: no cover - checked by validate() first
        raise ValidationError(t("Select the start date"))
    loan = Loan(acc.id, 0.0, inp.start, 0.0, inp.first_due, inp.first_due)
    return ctx.store.loans.add(_apply(ctx, loan, inp))


def planned_next_due(ctx: Context, inp: LoanInput, editing_id: str = "") -> date:
    """The first payment `edit` leaves to be handled: a loan that has handled none starts with the new first date;
    one that has continues after the last payment it booked or skipped, on the new schedule."""
    first = inp.first_due or date.today()
    old = ctx.store.loans.get(editing_id) if editing_id else None
    if old is None or old.next_due == old.first_due:
        return first
    handled = previous_before(old.first_due, old.recur_months, old.next_due)
    return next_after(first, inp.recur_months, handled) if handled else first


@recorded(N_("Change a loan"))
def edit(ctx: Context, loan_id: str, inp: LoanInput) -> Loan:
    """Change a loan; payments already booked stay as they are, the schedule continues on the new terms."""
    loan = get(ctx, loan_id)
    validate(ctx, inp, loan_id)
    acc = account_of(ctx, loan)
    acc_svc.edit_account(ctx, acc.id, inp.name, inp.currency)  # refuses another currency while in use
    loan.next_due = planned_next_due(ctx, inp, loan_id)
    return ctx.store.loans.update(_apply(ctx, loan, inp))


@recorded(N_("Delete a loan"))
def delete(ctx: Context, loan_id: str) -> None:
    """Delete a loan and its account; refused while transactions use the account or were booked for the loan."""
    loan = get(ctx, loan_id)
    acc = ctx.store.accounts.get(loan.account_id)
    used = any(
        tx.loan_id == loan.id or loan.account_id in (tx.from_account, tx.to_account)
        for tx in ctx.store.transactions.all()
    )
    if used:
        raise ValidationError(
            t("This loan has transactions. Delete them first, or keep the loan; a paid-off loan is shown as such.")
        )
    if acc is not None and any(acc.id in (r.from_account, r.to_account) for r in ctx.store.recurring.all()):
        raise ValidationError(t("A recurring transaction uses this account. Change or delete it first."))
    ctx.store.loans.delete([loan.id])
    if acc is not None:
        acc_svc.delete_account(ctx, acc.id)


# ------------------------------------------------------------------ due payments
def due(ctx: Context, today: date, loan_id: str = "") -> list[DuePayment]:
    """Payments due on or before `today`, oldest first, with the interest on the debt before each (a loan not looked
    after for a while has one per missed date). A paid-off loan has none."""
    out: list[DuePayment] = []
    for loan in loans(ctx):
        if (loan_id and loan.id != loan_id) or loan.next_due > today:
            continue
        owed = debt(ctx, loan, loan.next_due - timedelta(days=1))
        planned = plan.schedule(owed, loan.rate, loan.recur_months, loan.amortization, loan.next_due, until=today)
        for n, p in enumerate(planned):
            out.append(DuePayment(loan, p.date, p.interest, p.amortization, first=n == 0))
    return sorted(out, key=lambda d: (d.date, d.loan.id))


def due_count(ctx: Context, today: date) -> int:
    return len(due(ctx, today))


def _handle(ctx: Context, loan_id: str, on: date) -> Loan:
    """The loan, checked that `on` is the payment to handle next; advanced past it (not saved yet)."""
    loan = get(ctx, loan_id)
    if on < loan.next_due:
        raise ValidationError(t("This payment has already been booked or skipped"))
    if on > loan.next_due:
        raise ValidationError(t("Book or skip the earlier payments of this loan first"))
    loan.next_due = next_after(loan.first_due, loan.recur_months, on)
    return loan


@recorded(N_("Book a loan payment"))
def book(
    ctx: Context,
    loan_id: str,
    on: date,
    interest: float | None = None,
    amortization: float | None = None,
    booking_date: date | None = None,
) -> list[Transaction]:
    """Book the payment due `on`: the amortization as a transfer into the loan account and the interest as an
    expenditure, both from the paying account and dated `booking_date` (default `on`). Amounts not given are the
    schedule's. Returns the transactions booked (none for a payment of 0)."""
    loan = _handle(ctx, loan_id, on)
    acc = account_of(ctx, loan)
    owed = debt(ctx, loan, on - timedelta(days=1))
    if interest is None:
        interest = plan.interest(owed, loan.rate, loan.recur_months)
    if amortization is None:
        amortization = round(min(loan.amortization, max(owed, 0.0)), 2)
    interest, amortization = round(float(interest), 2), round(float(amortization), 2)
    if interest < 0 or amortization < 0:
        raise ValidationError(t("Interest and repayment can’t be negative"))
    if amortization > owed + EPS:
        raise ValidationError(
            t("The repayment exceeds the outstanding debt ({amount})", amount=f"{owed:,.2f} {acc.currency}")
        )
    day = booking_date or on
    out = []
    if amortization >= EPS:
        inp = TransactionInput(
            date=day,
            type=TX_TRANSFER,
            amount=amortization,
            currency=acc.currency,
            from_account=loan.cash_account,
            to_account=acc.id,
            comment=t("Repayment {name}", name=acc.name),
            loan_id=loan.id,
        )
        out.append(tx_svc.save(ctx, inp))
    if interest >= EPS:
        inp = TransactionInput(
            date=day,
            type=TX_EXPENDITURE,
            amount=interest,
            currency=acc.currency,
            category=loan.interest_category,
            from_account=loan.cash_account,
            payee=loan.lender,
            comment=t("Interest {name}", name=acc.name),
            loan_id=loan.id,
        )
        out.append(tx_svc.save(ctx, inp))
    ctx.store.loans.update(loan)
    return out


@recorded(N_("Skip a loan payment"))
def skip(ctx: Context, loan_id: str, on: date) -> Loan:
    """Drop the payment due `on`: nothing is booked for it."""
    return ctx.store.loans.update(_handle(ctx, loan_id, on))


@recorded(N_("Book loan payments"))
def book_all(ctx: Context, today: date, changes: Changes | None = None) -> Booking:
    """Book every due payment with the schedule's amounts unless `changes` has others; a loan whose payment can't be
    booked is left due with its later payments."""
    result = Booking()
    failed: set[str] = set()
    for d in due(ctx, today):
        if d.loan.id in failed:
            continue
        interest, amortization, day = (changes or {}).get((d.loan.id, d.date), (None, None, None))
        try:
            result.booked += book(ctx, d.loan.id, d.date, interest, amortization, day)
        except (ValidationError, FxError) as exc:
            failed.add(d.loan.id)
            result.failed.append((d, str(exc)))
    return result
