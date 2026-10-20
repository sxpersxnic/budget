"""Category suggestions (F07): rules first, then what the payee had before. Local and explainable, no AI.

1. **Rules** ("Always categorize like this", `rules.py`): the longest rule text found in the payee or the bank
   text / note wins, between equally long ones the newest (`domain/rules.py`).
2. **History:** without a matching rule, the category this payee had most often among the stored transactions of
   the same type; between equally frequent ones, the one used most recently. Transactions stored before payees
   existed (F09) have none, so for them the same bank text without its digits counts instead.

The account is suggested only where the caller does not fix it (`account=None`): an import knows its account,
the form may not. It comes from the rule when the rule names one, otherwise from the payee's history in the same
way as the category.

A suggestion is only ever shown: the form fills it in on a click, the import preview marks it until the user
confirms it by importing. Confirming creates no rule (decision 8). Nothing here writes.

`Suggester` reads the rules and the history once, so an import of many rows (or a form that asks on every key
stroke) looks each one up in a dictionary; `suggest` is the one-off shortcut.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from budget_app.domain.models import TX_EXPENDITURE, Rule, Transaction
from budget_app.domain.payees import key
from budget_app.domain.rules import by_priority, first_match
from budget_app.i18n import t, tn
from budget_app.services import rules as rules_svc
from budget_app.services.context import Context

RULE, PAYEE, TEXT = "rule", "payee", "text"


@dataclass(frozen=True)
class Suggestion:
    category: str
    account: str = ""  # only when the caller left the account open
    reason: str = ""  # why, in the interface language: "Rule: contains “migros”", "Used 14 times for this payee"
    source: str = ""  # RULE, PAYEE or TEXT


@dataclass
class _Tally:
    count: int = 0
    last: tuple[date, int] = (date.min, -1)  # date and position of the latest use, for ties


Tallies = dict[str, _Tally]


def _add(tallies: Tallies, value: str, when: tuple[date, int]) -> None:
    tally = tallies.setdefault(value, _Tally())
    tally.count += 1
    tally.last = max(tally.last, when)


def _top(tallies: Tallies | None) -> tuple[str, int] | None:
    """The most frequent value and its count; between equals, the most recently used."""
    if not tallies:
        return None
    value, tally = max(tallies.items(), key=lambda kv: (kv[1].count, kv[1].last))
    return value, tally.count


def text_key(text: str) -> str:
    """A bank text or note without case, accents, punctuation and digits ("Coop Pronto 1234" = "coop pronto")."""
    return key(re.sub(r"\d+", " ", text or ""))


def _account_of(tx: Transaction) -> str:
    return tx.from_account if tx.type == TX_EXPENDITURE else tx.to_account


class Suggester:
    """Suggestions from the rules and the history as they are when it is created."""

    def __init__(self, ctx: Context):
        self.ctx = ctx
        stored = [r for r in ctx.store.rules.all() if rules_svc.valid_category(ctx, r.type, r.category)]
        self._rules = by_priority(stored)
        self._by_payee: dict[tuple[str, str], Tallies] = defaultdict(dict)  # (type, payee key) -> categories
        self._accounts: dict[tuple[str, str], Tallies] = defaultdict(dict)  # (type, payee key) -> accounts
        self._by_text: dict[tuple[str, str], Tallies] = defaultdict(dict)  # (type, text key), rows without payee
        for pos, tx in enumerate(ctx.store.transactions.all()):
            if tx.type not in rules_svc.RULE_TYPES or not tx.category or tx.is_advance:
                continue  # repayments have no category
            when = (tx.date, pos)
            if tx.payee:
                k = (tx.type, key(tx.payee))
                _add(self._by_payee[k], tx.category, when)
                if account := _account_of(tx):
                    _add(self._accounts[k], account, when)
            elif tx.comment and (tk := text_key(tx.comment)):
                _add(self._by_text[(tx.type, tk)], tx.category, when)

    def suggest(self, type_: str, payee: str, text: str, account: str | None = None) -> Suggestion | None:
        """The suggestion for a transaction of `type_` (expenditure or income), or None.

        `account=None` asks for an account too; an account given (also "") is the caller's own.
        """
        if type_ not in rules_svc.RULE_TYPES:
            return None
        payee_key = (type_, key(payee)) if key(payee) else None
        rule = first_match(self._rules, type_, payee, text)
        if rule is not None:
            return Suggestion(
                rule.category,
                self._account(rule, payee_key) if account is None else "",
                t("Rule: contains “{match}”", match=rule.match),
                RULE,
            )
        if payee_key and (top := _top(self._by_payee.get(payee_key))):
            category, n = top
            return Suggestion(
                category,
                self._account(None, payee_key) if account is None else "",
                tn("Used {n} time for this payee", "Used {n} times for this payee", n),
                PAYEE,
            )
        tk = text_key(text)
        if tk and (top := _top(self._by_text.get((type_, tk)))):
            category, n = top
            return Suggestion(category, "", tn("Used {n} time for this text", "Used {n} times for this text", n), TEXT)
        return None

    def _account(self, rule: Rule | None, payee_key: tuple[str, str] | None) -> str:
        if rule is not None and rule.account:
            return rule.account if rules_svc.valid_account(self.ctx, rule.account) else ""
        top = _top(self._accounts.get(payee_key)) if payee_key else None
        return top[0] if top and rules_svc.valid_account(self.ctx, top[0]) else ""


def suggest(ctx: Context, type_: str, payee: str, text: str, account: str | None = None) -> Suggestion | None:
    """The category (and, with `account=None`, the account) to suggest for one transaction; see `Suggester`."""
    return Suggester(ctx).suggest(type_, payee, text, account)
