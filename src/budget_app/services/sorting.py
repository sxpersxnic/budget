"""To sort: spending and income without a category, asked about once per payee.

An import brings in every new row, with a category or without one. What has none waits here, grouped by payee
(or, without a payee, by the bank text), so a file with 1'000 such rows asks a few dozen questions, not 1'000.
Each group knows what kind of question to ask:

- **shop**: "What do you usually buy here?" The likeliest answers first: what a multi-purpose shop usually is
  (`resources/shop_answers.json`: Amazon → devices, books, household, clothing), the category a search word in
  the payee or the text points to (F08); only when nothing is known, the categories used most.
- **ATM**: cash from a cash machine (`ATM_WORDS` in the text). Answered like a shop for now.
- **person**: a TWINT payment to someone who is not a brand ("Sandro M."): gifts and eating out first.
- **income**: money in, answered with the income categories used most.

`answer` gives every transaction of a group the chosen category in one undoable step, through `bulk.bulk_edit`
(every row checked like an edit), and with `remember` adds a rule for the payee, so the next import sorts it.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import date
from functools import cache

from budget_app.config import RESOURCES_DIR
from budget_app.domain.coicop import TAX, get_coicop
from budget_app.domain.everyday import keyword_category
from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME, Transaction
from budget_app.domain.payees import brand, key
from budget_app.i18n import N_
from budget_app.services import bulk
from budget_app.services import rules as rules_svc
from budget_app.services.context import Context
from budget_app.services.undo import recorded

SHOP, ATM, PERSON, INCOME = "shop", "atm", "person", "income"
FILTER = "to-sort"  # the transaction list's category filter for what is to sort (an empty value is no filter)
ATM_WORDS = ("bancomat", "geldautomat", "atm", "bargeld", "bargeldbezug", "postomat", "retrait", "prelevamento")
GIFTS, EATING_OUT = "09.2.1.3", "11.1"
CHOICES = 4  # answers offered before "Something else…"
SHOP_ANSWERS_FILE = RESOURCES_DIR / "shop_answers.json"
# "Sandro M.", "Anna Muster": a person's name, as TWINT puts it in the bank text
_PERSON = re.compile(r"^[A-ZÄÖÜ][\w'-]+(?: [A-ZÄÖÜ][\w'-]*\.?){1,2}$")


@dataclass
class Group:
    """The transactions of one payee and type that have no category."""

    type: str
    name: str  # the payee, else the bank text, as shown
    ids: list[str] = field(default_factory=list)
    total: float = 0.0  # in CHF, positive: money out for spending, money in for income
    first: date | None = None
    last: date | None = None
    kind: str = SHOP
    choices: list[str] = field(default_factory=list)  # category codes or income category ids, likeliest first

    @property
    def count(self) -> int:
        return len(self.ids)


def is_unsorted(tx: Transaction) -> bool:
    """Spending or income that waits for a category. Advance payments and repayments have their own."""
    return tx.type in (TX_EXPENDITURE, TX_INCOME) and not tx.category and not tx.is_advance and not tx.settles_id


def unsorted(ctx: Context) -> list[Transaction]:
    return [tx for tx in ctx.store.transactions.all() if is_unsorted(tx)]


def groups(ctx: Context, ids: Collection[str] | None = None) -> list[Group]:
    """The unsorted transactions (of `ids`, when given) by payee and type, the largest group first."""
    found: dict[tuple[str, str], Group] = {}
    for tx in unsorted(ctx):
        if ids is not None and tx.id not in ids:
            continue
        name = tx.payee or tx.comment or "?"
        g = found.setdefault((tx.type, key(name)), Group(tx.type, name))
        g.ids.append(tx.id)
        g.total += tx.amount_chf
        g.first = min(g.first, tx.date) if g.first else tx.date
        g.last = max(g.last, tx.date) if g.last else tx.date
    out = sorted(found.values(), key=lambda g: (-g.count, -g.total, key(g.name)))
    texts = {tx.id: tx.comment for tx in ctx.store.transactions.all()}
    usage = _usage(ctx)
    for g in out:
        text = " ".join(texts.get(i, "") for i in g.ids[:3])
        g.kind = _kind(g, text)
        g.choices = _choices(g, text, usage)
    return out


def count(ctx: Context) -> int:
    """How many payees wait for an answer: the number in the sidebar."""
    return len({(tx.type, key(tx.payee or tx.comment)) for tx in unsorted(ctx)})


def _kind(g: Group, text: str) -> str:
    if g.type == TX_INCOME:
        return INCOME
    words = key(text).split()
    if any(w in words for w in ATM_WORDS):
        return ATM
    if "twint" in words and not brand(key(g.name).split()) and _PERSON.match(g.name.strip()):
        return PERSON
    return SHOP


@cache
def _shop_answers() -> dict[str, list[str]]:
    return json.loads(SHOP_ANSWERS_FILE.read_text(encoding="utf-8"))


def _usage(ctx: Context) -> dict[str, list[str]]:
    """Per type, the categories used most, most first."""
    counts: dict[str, Counter[str]] = {TX_EXPENDITURE: Counter(), TX_INCOME: Counter()}
    for tx in ctx.store.transactions.all():
        if tx.type in counts and tx.category and not tx.is_advance and not tx.settles_id:
            counts[tx.type][tx.category] += 1
    return {t: [c for c, _ in n.most_common()] for t, n in counts.items()}


def _choices(g: Group, text: str, usage: dict[str, list[str]]) -> list[str]:
    if g.kind == INCOME:
        return usage[TX_INCOME][:CHOICES]
    picks: list[str] = []
    if g.kind == PERSON:
        picks += [GIFTS, EATING_OUT]
    if (b := brand(key(g.name).split())) and b in _shop_answers():
        picks += _shop_answers()[b]
    for source in (g.name, text):
        if hit := keyword_category(source):
            picks.append(hit[0])
    if not picks:  # nothing known about this payee: what is used most, rather than nothing
        picks += usage[TX_EXPENDITURE]
    codes = get_coicop()
    valid = [c for c in dict.fromkeys(picks) if c == TAX or codes.get(c) is not None]
    return valid[:CHOICES]


@recorded(N_("Sort transactions"))
def answer(ctx: Context, group: Group, category: str, remember: bool = False) -> int:
    """Give every transaction of `group` the category `category`; with `remember`, add a rule for the payee so
    later imports sort it. One undoable step. The number of transactions sorted."""
    changes = (
        bulk.BulkChanges(expenditure_category=category)
        if group.type == TX_EXPENDITURE
        else bulk.BulkChanges(income_category=category)
    )
    saved = bulk.bulk_edit(ctx, group.ids, changes)
    if remember:  # a rule with the same text takes the new answer
        rules_svc.save(ctx, rules_svc.RuleInput(match=group.name, type=group.type, category=category), replace=True)
    return len(saved)
