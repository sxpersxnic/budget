"""Persistent record types. Every money record stores its original amount and
currency, the amount in the base currency (CHF) and the ECB date of the rate used."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field

from budget_app.i18n import N_


def new_id() -> str:
    return uuid.uuid4().hex[:12]


ACCOUNT_REGULAR = "regular"
ACCOUNT_ADVANCE = "advance"

TX_EXPENDITURE = "expenditure"
TX_INCOME = "income"
TX_TRANSFER = "transfer"
TX_EXCHANGE = "exchange"
TX_REVALUATION = "revaluation"  # a change in value that is neither income nor spending (F43)
TX_TYPES = {
    TX_EXPENDITURE: N_("Expenditure"),
    TX_INCOME: N_("Income"),
    TX_TRANSFER: N_("Account transfer"),
    TX_EXCHANGE: N_("Currency exchange"),
    TX_REVALUATION: N_("Revaluation"),
}
# kinds of revaluation, stored in `category`
REVAL_FX = "REVAL_FX"
REVAL_INVEST = "REVAL_INVEST"
REVALUATION_KINDS = {
    REVAL_FX: N_("Capital gains on currency exchange"),
    REVAL_INVEST: N_("Capital gains on investment"),
}

BUDGET_INCOME = "income"
BUDGET_EXPENDITURE = "expenditure"

# asset classes of regular accounts (F21); advance-payment accounts are always cash
ASSET_CASH = "cash"
ASSET_SECURITIES = "securities"
ASSET_PILLAR2 = "pillar2"
ASSET_PILLAR3A = "pillar3a"
ASSET_REAL_ESTATE = "real_estate"
ASSET_CRYPTO = "crypto"
ASSET_OTHER = "other"
ASSET_LOAN = "loan"  # F22: a liability; its balance is the debt a `Loan` tracks, negative
ASSET_CLASSES = {
    ASSET_CASH: N_("Cash and accounts"),
    ASSET_SECURITIES: N_("Securities"),
    ASSET_PILLAR2: N_("Pension fund (pillar 2)"),
    ASSET_PILLAR3A: N_("Pillar 3a"),
    ASSET_REAL_ESTATE: N_("Real estate"),
    ASSET_CRYPTO: N_("Crypto"),
    ASSET_OTHER: N_("Other assets"),
    ASSET_LOAN: N_("Loans and mortgages"),
}
# accounts of these classes are valued from their holdings (quantity × price), not from recorded balances
HOLDING_CLASSES = (ASSET_SECURITIES, ASSET_CRYPTO)

TRADE_BUY = "buy"
TRADE_SELL = "sell"
TRADE_DIVIDEND = "dividend"
TRADE_KINDS = {TRADE_BUY: N_("Buy"), TRADE_SELL: N_("Sell"), TRADE_DIVIDEND: N_("Dividend")}
# the quantity and price a holding starts with when an account with history becomes a securities or crypto
# account (decision 13); booked by the conversion, not chosen in the trade sheet
TRADE_OPENING = "opening"


@dataclass
class Account:
    name: str
    currency: str
    kind: str = ACCOUNT_REGULAR
    created: dt.date = field(default_factory=dt.date.today)
    asset_class: str = ASSET_CASH  # F21; files written before it have no such column: cash
    # the month (YYYY-MM) an account with history became a securities or crypto account (decision 13); empty for
    # accounts that were one from the start, and in files written before it
    invest_from: str = ""
    id: str = field(default_factory=new_id)

    @property
    def holds_securities(self) -> bool:
        """Valued from its holdings (F21): securities and crypto accounts."""
        return self.asset_class in HOLDING_CLASSES

    def holds_in(self, month: str) -> bool:
        """Valued from its holdings at the end of `month`: a securities or crypto account, from the month it was
        converted on (before it, its recorded balances count)."""
        return self.holds_securities and (not self.invest_from or month >= self.invest_from)

    @property
    def is_loan(self) -> bool:
        """A loan's liability account (F22): its balance is the outstanding debt, negative, computed from the
        loan and the transactions on the account; no balances are recorded for it."""
        return self.asset_class == ASSET_LOAN


@dataclass
class IncomeCategory:
    name: str
    id: str = field(default_factory=new_id)


@dataclass
class Transaction:
    date: dt.date
    type: str
    amount: float  # always positive, in `currency`
    currency: str
    amount_chf: float
    fx_date: dt.date  # ECB date of the rate used for amount_chf
    category: str = ""  # COICOP code / TAX (expenditure), income category id (income), REVAL_* (revaluation)
    # revaluation: a gain is booked to `to_account`, a loss from `from_account` (amount stays positive)
    from_account: str = ""
    to_account: str = ""
    to_amount: float = 0.0  # currency exchange: amount credited in the to-account currency
    to_currency: str = ""
    is_advance: bool = False
    advance_amount: float = 0.0  # expenditure: share paid on behalf of others (in `currency`)
    advance_account: str = ""
    debtor: str = ""
    settles_id: str = ""  # income repayment: id of the advanced expenditure being settled
    comment: str = ""
    # set for imported rows: identifies the source row, so a second import skips it
    import_ref: str = ""
    # an exchange and the revaluation booking its spread against the ECB rate point at each other (F44)
    linked_id: str = ""
    recurring_id: str = ""  # set when booked from a recurring transaction (F05): the series' id
    payee: str = ""  # who was paid or who paid (F09): the name of a `Payee`; empty for transfers and older rows
    # booked by the Investments page (F21): the id of the `Trade` or `Price` the row belongs to
    invest_id: str = ""
    tags: str = ""  # F10: ids of `Tag`s, separated by "|" (domain.tags.split / join); empty in older files
    loan_id: str = ""  # F22: the `Loan` whose payment booked the row (repayment or interest); empty in older files
    id: str = field(default_factory=new_id)


ALIAS_SEP = "|"


@dataclass
class Payee:
    """A merchant or counterparty (F09), such as "Migros".

    `aliases` are lookup keys (`domain.payees.key`, folded) that lead to this payee, separated by "|": the
    cleaned bank texts imported for it and its former names. Renaming and merging keep them, so the next
    import of the same text lands on the payee the user chose.
    """

    name: str
    aliases: str = ""
    id: str = field(default_factory=new_id)

    @property
    def alias_list(self) -> list[str]:
        return [a for a in self.aliases.split(ALIAS_SEP) if a]

    def set_aliases(self, aliases: list[str]) -> None:
        self.aliases = ALIAS_SEP.join(sorted(dict.fromkeys(a for a in aliases if a)))


TAG_COLOURS = {  # F10: the fixed palette of tag colours; the shades for light and dark mode are in ui/theme.py
    "grey": N_("Grey"),
    "red": N_("Red"),
    "orange": N_("Orange"),
    "yellow": N_("Yellow"),
    "green": N_("Green"),
    "teal": N_("Teal"),
    "blue": N_("Blue"),
    "purple": N_("Purple"),
    "pink": N_("Pink"),
}
TAG_DEFAULT_COLOUR = "grey"


@dataclass
class Tag:
    """A label across categories (F10), such as "Holiday Italy 2026" or "Renovation".

    Transactions, templates and recurring series name their tags by id (`Transaction.tags`), so renaming a tag
    changes one row here. A tag can have a budget: `budget` in `budget_currency` for the tagged spending between
    `budget_start` and `budget_end` (each 'YYYY-MM-DD' or empty for an open end); `budget` 0 means no budget.
    """

    name: str
    colour: str = TAG_DEFAULT_COLOUR  # a key of TAG_COLOURS
    budget: float = 0.0
    budget_currency: str = ""
    budget_start: str = ""
    budget_end: str = ""
    id: str = field(default_factory=new_id)

    @property
    def has_budget(self) -> bool:
        return self.budget > 0

    @property
    def start_date(self) -> dt.date | None:
        return dt.date.fromisoformat(self.budget_start) if self.budget_start else None

    @property
    def end_date(self) -> dt.date | None:
        return dt.date.fromisoformat(self.budget_end) if self.budget_end else None


@dataclass
class Rule:
    """A categorization rule (F07, "Always categorize like this"): a text that, found in the payee or the bank
    text, suggests a category.

    `match` is compared folded (case and accents ignored). `category` is a COICOP code or `TAX` for expenditure,
    an income category id for income. `account` is suggested along with it when the transaction has none yet:
    the From account of expenditure, the To account of income.
    """

    match: str
    type: str = TX_EXPENDITURE  # TX_EXPENDITURE or TX_INCOME
    category: str = ""
    account: str = ""  # optional
    id: str = field(default_factory=new_id)


@dataclass
class Template:
    """A saved starting point for a new transaction, such as "Coffee · 01.1.1 · Cash · 4.50" (F06)."""

    name: str
    type: str = TX_EXPENDITURE
    amount: float = 0.0  # 0: no amount, the user types it
    currency: str = ""
    category: str = ""
    from_account: str = ""
    to_account: str = ""
    comment: str = ""
    payee: str = ""  # F09
    tags: str = ""  # F10: tag ids separated by "|"
    id: str = field(default_factory=new_id)


@dataclass
class TransactionItem:
    """One line of a transaction's product list (e.g. a receipt), in the transaction's currency.

    A line of an expenditure may have its own `category` and `tags` (F11, decision 14): it is then a split
    line, and reports count its total in that category (`domain.splits`). Files written before F11 have
    neither column: every line keeps the transaction's category.
    """

    transaction_id: str
    name: str
    quantity: float = 1.0
    unit_price: float = 0.0  # negative for discounts
    position: int = 0  # order within the list
    category: str = ""  # F11: a COICOP code or TAX; empty: the transaction's category
    tags: str = ""  # F11: ids of `Tag`s, separated by "|", counted for this line on top of the transaction's
    id: str = field(default_factory=new_id)

    @property
    def total(self) -> float:
        return round(self.quantity * self.unit_price, 2)


@dataclass
class Attachment:
    """A file kept with a transaction (F12): a receipt, an invoice, a contract; or with a QR bill (F03).

    The file lies in `attachments/<folder>/<stored>` in the data folder; `name` is the name it had when
    it was added, only shown, never used as a path. A bill's file has `bill_id` and lies in the bill's
    folder; paying the bill sets `transaction_id`, so the file then belongs to the payment as well and
    never moves (architecture §7, decision 22).
    """

    transaction_id: str  # empty while the bill it came with is open
    name: str
    stored: str  # "<id>.<extension>" (domain.attachments.stored_name)
    size: int = 0  # bytes
    content_type: str = ""
    added: dt.date = field(default_factory=dt.date.today)
    bill_id: str = ""  # F03: the bill the file came with; older files have no such column
    id: str = field(default_factory=new_id)

    @property
    def folder(self) -> str:
        """The folder under `attachments/` the file lies in: the bill's, else the transaction's."""
        return self.bill_id or self.transaction_id


@dataclass
class TaxSection:
    """The tags linked to a section of the tax summary (F20): transactions with one of them count in that section,
    on top of its categories. `id` is the section's key (`domain.tax`); a tag is linked to one section at most."""

    tags: str = ""  # tag ids separated by "|"
    id: str = field(default_factory=new_id)


BILL_OPEN = "open"
BILL_PAID = "paid"
BILL_DISMISSED = "dismissed"


@dataclass
class Bill:
    """A scanned Swiss QR bill (F03, decision 15): open until it is paid (an expenditure is booked) or dismissed.

    Its file is an `Attachment` with this bill's id. `amount` 0 means the bill leaves the amount to the payer;
    `due` is 'YYYY-MM-DD' (from the bill information, or set by the user) or empty.
    """

    creditor: str
    iban: str
    currency: str
    amount: float = 0.0
    creditor_address: str = ""  # one line: "Rue du Lac 1268, 2501 Biel, CH"
    reference_type: str = ""  # QRR, SCOR or NON
    reference: str = ""
    message: str = ""
    bill_info: str = ""
    due: str = ""
    status: str = BILL_OPEN
    transaction_id: str = ""  # the expenditure that paid it
    added: dt.date = field(default_factory=dt.date.today)
    id: str = field(default_factory=new_id)

    @property
    def due_date(self) -> dt.date | None:
        return dt.date.fromisoformat(self.due) if self.due else None


GOAL_AMOUNT = "amount"  # a target amount, such as "Holiday 4'000 CHF by June"
GOAL_MONTHS = "months"  # N months of spending (an emergency fund): the target follows the recorded spending


@dataclass
class Goal:
    """A savings goal (F14).

    `kind` GOAL_AMOUNT aims at `target` in `currency`; GOAL_MONTHS at `months` × the average monthly spending of
    the last 12 complete months, in `currency` (`services.goals.monthly_spending`). Progress is the balance of
    `account_id` when linked, else `saved`, the amount saved so far typed in by hand. `target_date`
    ('YYYY-MM-DD') is optional. `start` and `start_amount` are where the on-track line begins: the day the goal
    was added (or linked to another account) and what it held then. `reserve` keeps the goal's money out of the
    projected free wealth on the Overview.
    """

    name: str
    currency: str
    kind: str = GOAL_AMOUNT
    target: float = 0.0
    months: int = 0
    target_date: str = ""
    account_id: str = ""
    saved: float = 0.0
    reserve: bool = False
    archived: bool = False
    start: dt.date = field(default_factory=dt.date.today)
    start_amount: float = 0.0
    id: str = field(default_factory=new_id)

    @property
    def due(self) -> dt.date | None:
        return dt.date.fromisoformat(self.target_date) if self.target_date else None


@dataclass
class AccountEntry:
    """Recorded balance of an account at the end of a month."""

    account_id: str
    month: str
    amount: float
    currency: str
    amount_chf: float
    fx_date: dt.date
    id: str = field(default_factory=new_id)


@dataclass
class BudgetEntry:
    """One budgeted amount for one month. Entries created together share a series_id."""

    month: str
    type: str
    category: str
    amount: float
    currency: str
    amount_chf: float
    fx_date: dt.date
    series_id: str = ""
    recur_months: int = 1
    forever: bool = False
    comment: str = ""
    generated_until: str = ""  # 'Forever' series: horizon up to which rows were generated
    id: str = field(default_factory=new_id)


@dataclass
class Recurring:
    """A transaction that repeats (F05): its template and its schedule.

    Occurrences fall on the start's day of the month, every `recur_months` months (the last day of
    a shorter month for days past its end). `next_due` is the first occurrence not yet booked or
    skipped; everything before it has been handled.
    """

    type: str  # TX_EXPENDITURE, TX_INCOME or TX_TRANSFER
    amount: float
    currency: str
    start: dt.date  # first occurrence; its day of the month is the day of every occurrence
    next_due: dt.date
    category: str = ""
    from_account: str = ""
    to_account: str = ""
    comment: str = ""
    recur_months: int = 1
    end: str = ""  # last day an occurrence may fall on ('YYYY-MM-DD'); empty: no end
    auto: bool = False  # book due occurrences without asking
    payee: str = ""  # F09
    tags: str = ""  # F10: tag ids separated by "|"
    id: str = field(default_factory=new_id)

    @property
    def end_date(self) -> dt.date | None:
        return dt.date.fromisoformat(self.end) if self.end else None


@dataclass
class Holding:
    """A security or coin held in a securities or crypto account (F21), in the account's currency.

    Its quantity comes from its trades; its value is that quantity times the latest price."""

    account_id: str
    name: str
    symbol: str = ""  # ISIN, ticker or symbol; optional
    id: str = field(default_factory=new_id)


@dataclass
class Trade:
    """A buy, sale or dividend of a holding (F21), paid from or to a cash account in the holding's currency.

    A buy or sale moves quantity × price between the cash account and the investment account, and its price
    counts as a price of that day; a fee is spending from the cash account. A dividend is income of the gross
    `amount` with its withholding `tax` booked as Taxes, so the cash account receives the net."""

    holding_id: str
    date: dt.date
    kind: str  # TRADE_BUY, TRADE_SELL, TRADE_DIVIDEND or TRADE_OPENING
    quantity: float = 0.0  # up to 6 decimals; 0 for a dividend
    price: float = 0.0  # per unit, up to 6 decimals; 0 for a dividend
    fee: float = 0.0
    amount: float = 0.0  # dividend: the gross amount
    tax: float = 0.0  # dividend: the withholding tax (decision 12); files written before it have none: 0
    cash_account: str = ""
    comment: str = ""
    seq: int = 0  # order of entry: trades of the same day count in this order
    id: str = field(default_factory=new_id)


@dataclass
class Price:
    """The price of a holding on a day (F21), typed in or pasted from a price list; one per holding and day."""

    holding_id: str
    date: dt.date
    price: float
    id: str = field(default_factory=new_id)


@dataclass
class Loan:
    """A loan or mortgage (F22): a liability account with a repayment schedule.

    The account (`account_id`, asset class `loan`) owes `principal` on `start`; what is transferred into the
    account pays the debt off, what leaves it adds to the debt, so its balance is always minus the outstanding
    debt (`services.loans.debt`). Every `recur_months` months from `first_due` a payment is due: interest on the
    outstanding debt (`rate` % a year, simple interest for the period) as an expenditure in `interest_category`,
    and `amortization` as a transfer from `cash_account` (0 for an interest-only mortgage). `next_due` is the
    first payment not yet booked or skipped. `fixed_until` ('YYYY-MM-DD' or empty) is when the rate or the
    term ends (a mortgage renewal); it is shown, nothing more.
    """

    account_id: str
    principal: float
    start: dt.date
    rate: float  # percent a year
    first_due: dt.date
    next_due: dt.date
    recur_months: int = 1
    amortization: float = 0.0  # per payment, in the account's currency
    cash_account: str = ""  # the regular account the payments come from
    interest_category: str = "12.2.1"  # domain.loans.INTEREST_CATEGORY
    lender: str = ""  # the payee of the interest (F09); optional
    fixed_until: str = ""
    comment: str = ""
    id: str = field(default_factory=new_id)

    @property
    def fixed_until_date(self) -> dt.date | None:
        return dt.date.fromisoformat(self.fixed_until) if self.fixed_until else None


@dataclass
class Settings:
    budget_horizon: str = ""
    display_currency: str = "CHF"
    last_fx_download: str = ""
    language: str = ""  # "" = follow the system; otherwise a key of i18n.LANGUAGES
