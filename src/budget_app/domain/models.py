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


@dataclass
class Account:
    name: str
    currency: str
    kind: str = ACCOUNT_REGULAR
    created: dt.date = field(default_factory=dt.date.today)
    id: str = field(default_factory=new_id)


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
    id: str = field(default_factory=new_id)


@dataclass
class TransactionItem:
    """One line of a transaction's product list (e.g. a receipt), in the transaction's currency."""

    transaction_id: str
    name: str
    quantity: float = 1.0
    unit_price: float = 0.0  # negative for discounts
    position: int = 0  # order within the list
    id: str = field(default_factory=new_id)

    @property
    def total(self) -> float:
        return round(self.quantity * self.unit_price, 2)


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
class Settings:
    budget_horizon: str = ""
    display_currency: str = "CHF"
    last_fx_download: str = ""
    language: str = ""  # "" = follow the system; otherwise a key of i18n.LANGUAGES
