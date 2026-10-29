"""Subscription and recurring-spend detection (F17): charges that repeat at the same rhythm with similar amounts.

Expenditure is grouped by payee (F09, compared by `domain.payees.key`; a row without a payee by its note) and
currency; `domain.subscriptions.detect` decides whether a group's latest charges make a subscription. Transactions
a recurring series booked (F05) are left out, and so is a group whose payee already has a running expenditure
series, or that the user told the page to ignore (`subscription_ignores.csv`). Detection only suggests: *Make
Recurring…* opens the series sheet prefilled from the last charge, and nothing is booked here (the spirit of
decision 16).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

from budget_app.config import BASE_CURRENCY
from budget_app.domain import subscriptions as rules
from budget_app.domain.coicop import fold
from budget_app.domain.models import TX_EXPENDITURE, Recurring, SubscriptionIgnore, Transaction
from budget_app.domain.payees import key as payee_key
from budget_app.i18n import N_, t
from budget_app.services import recurring as rec_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.recurring import RecurringInput
from budget_app.services.undo import recorded

NOTE_PREFIX = "note:"


@dataclass
class Subscription:
    """A detected subscription: who charges, how often, how much, and the charges it was found in."""

    key: str  # the group: a payee key, or NOTE_PREFIX + the folded note
    name: str  # the payee as last written, or the note
    currency: str
    period: int  # months between charges
    amount: float  # the usual charge, in `currency`
    first: date
    last: date
    count: int
    category: str  # the run's most frequent category
    account: str  # the account most of the run's charges were paid from
    transaction_ids: list[str] = field(default_factory=list)  # the run's charges, oldest first
    yearly_view: float = 0.0  # a year of charges in the view currency, at the latest rate

    @property
    def yearly(self) -> float:
        return self.amount * 12 / self.period

    @property
    def next(self) -> date:
        return rules.next_charge(self.last, self.period)

    @property
    def last_id(self) -> str:
        return self.transaction_ids[-1]


@dataclass(frozen=True)
class SeriesSpend:
    """A running expenditure series (F05) and what it costs a year."""

    series: Recurring
    title: str
    yearly: float  # in the series' currency
    yearly_view: float


def group_key(tx: Transaction) -> str:
    """How a charge is grouped: by its payee, else by its note; "" when it has neither."""
    if tx.payee:
        return payee_key(tx.payee)
    note = fold(tx.comment).strip()
    return NOTE_PREFIX + note if note else ""


def _series_key(series: Recurring) -> str:
    if series.payee:
        return payee_key(series.payee)
    note = fold(series.comment).strip()
    return NOTE_PREFIX + note if note else ""


def groups(ctx: Context) -> dict[tuple[str, str], list[Transaction]]:
    """Expenditure not booked by a series, per (group key, currency), oldest first."""
    out: dict[tuple[str, str], list[Transaction]] = defaultdict(list)
    for tx in sorted(ctx.store.transactions.all(), key=lambda x: (x.date, x.id)):
        if tx.type != TX_EXPENDITURE or tx.recurring_id:
            continue
        if k := group_key(tx):
            out[(k, tx.currency)].append(tx)
    return dict(out)


def already_recurring(ctx: Context) -> set[tuple[str, str]]:
    """The groups a running expenditure series already covers."""
    return {
        (k, s.currency)
        for s in ctx.store.recurring.all()
        if s.type == TX_EXPENDITURE and not rec_svc.ended(s) and (k := _series_key(s))
    }


def ignored(ctx: Context) -> list[SubscriptionIgnore]:
    return sorted(ctx.store.subscription_ignores.all(), key=lambda i: (i.name.lower(), i.currency, i.id))


def _ignored_keys(ctx: Context) -> set[tuple[str, str]]:
    return {(i.key, i.currency) for i in ctx.store.subscription_ignores.all()}


def _to_view(ctx: Context, amount: float, currency: str, view_cur: str) -> float:
    if currency == view_cur:
        return amount
    return ctx.convert_on(ctx.convert_on(amount, currency, BASE_CURRENCY, None), BASE_CURRENCY, view_cur, None)


def detect(ctx: Context, view_cur: str, today: date | None = None) -> list[Subscription]:
    """The running subscriptions found in the transactions, the most expensive year first."""
    today = today or date.today()
    skip = already_recurring(ctx) | _ignored_keys(ctx)
    found: list[Subscription] = []
    for (k, currency), group in groups(ctx).items():
        if (k, currency) in skip:
            continue
        rhythm = rules.detect([(tx.date, tx.amount) for tx in group], today)
        if rhythm is None:
            continue
        txs = [tx for tx in group if tx.date >= rhythm.first]  # the run; older charges don't describe it
        last = txs[-1]
        sub = Subscription(
            k,
            last.payee or last.comment,
            currency,
            rhythm.period,
            rhythm.amount,
            rhythm.first,
            rhythm.last,
            rhythm.count,
            Counter(tx.category for tx in txs).most_common(1)[0][0],
            Counter(tx.from_account for tx in txs).most_common(1)[0][0],
            [tx.id for tx in txs],
        )
        sub.yearly_view = _to_view(ctx, sub.yearly, currency, view_cur)
        found.append(sub)
    return sorted(found, key=lambda s: (-s.yearly_view, s.name.lower()))


def recurring_spend(ctx: Context, view_cur: str) -> list[SeriesSpend]:
    """What the running expenditure series cost a year, the most expensive first."""
    out = [
        SeriesSpend(s, rec_svc.title(ctx, s), s.amount * 12 / s.recur_months, 0.0)
        for s in ctx.store.recurring.all()
        if s.type == TX_EXPENDITURE and not rec_svc.ended(s)
    ]
    out = [SeriesSpend(x.series, x.title, x.yearly, _to_view(ctx, x.yearly, x.series.currency, view_cur)) for x in out]
    return sorted(out, key=lambda x: (-x.yearly_view, x.title.lower()))


def to_recurring(ctx: Context, sub: Subscription) -> RecurringInput:
    """A series that continues the subscription: the last charge's template, repeating at the detected rhythm."""
    last = ctx.store.transactions.get(sub.last_id)
    if last is None:
        raise ValidationError(t("Transaction not found"))
    inp = rec_svc.from_transaction(last, sub.period)
    inp.category = sub.category
    inp.from_account = sub.account
    return inp


@recorded(N_("Ignore a subscription"))
def ignore(ctx: Context, sub: Subscription) -> SubscriptionIgnore:
    """Leave a detected subscription out of the list from now on."""
    for existing in ctx.store.subscription_ignores.all():
        if (existing.key, existing.currency) == (sub.key, sub.currency):
            return existing
    return ctx.store.subscription_ignores.add(SubscriptionIgnore(sub.key, sub.currency, sub.name))


@recorded(N_("Watch a subscription again"))
def unignore(ctx: Context, ignore_id: str) -> int:
    if ctx.store.subscription_ignores.get(ignore_id) is None:
        raise ValidationError(t("This subscription is not ignored"))
    return ctx.store.subscription_ignores.delete([ignore_id])
