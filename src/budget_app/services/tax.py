"""Swiss tax summary (F20): a year's amounts per typical deduction area, with the transactions behind them.

The summary helps with the tax declaration without filing it and without judging what is deductible: it lists
amounts in CHF (the declaration's currency) and leaves the judgement to the user, as cantonal rules differ.

**What counts in a section** (`domain.tax.SECTIONS`, architecture §7, decision 25):

* expenditure by its category: each part of a split transaction (F11) in its own category, the share paid for
  others (an advance) left out;
* anything carrying a tag the user linked to the section (`set_links`; a tag belongs to one section at most): an
  expenditure part with the tag (on the transaction or on its split line), income and transfers with it;
* the rules of three sections: transfers to a pillar 3a account (F21 asset class) from an account of another class;
  the withholding tax of dividends in CHF accounts (`investments.reclaimable`); the gross dividends of every holding
  with their withholding tax, and income in a category called Dividends or Interest, for the securities list.

In a spending section income counts negative (a refund, say from the health insurer), in the income section
spending does. A transaction appears once per section, with the sum of its matching parts; it can be in several
sections only when its category matches one and a tag another (or two tags two others), since the code lists of the
sections don't overlap. The rows booked for a dividend (F21) come from the dividend itself, never twice.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from budget_app import i18n
from budget_app.config import BASE_CURRENCY
from budget_app.domain import investments as inv
from budget_app.domain import tags as tag_codec
from budget_app.domain.coicop import ADV, TAX, fold, get_coicop
from budget_app.domain.models import (
    ASSET_PILLAR3A,
    TRADE_DIVIDEND,
    TX_EXPENDITURE,
    TX_INCOME,
    TX_TRANSFER,
    Attachment,
    TaxSection,
    Trade,
    Transaction,
)
from budget_app.domain.tax import (
    BY_KEY,
    INCOME_NAMES,
    PILLAR3A,
    SECTIONS,
    SECURITIES,
    WITHHOLDING,
    Section,
    section_of_code,
)
from budget_app.i18n import N_, t
from budget_app.services import investments as inv_svc
from budget_app.services import tags as tag_svc
from budget_app.services.aggregates import split_lines, transaction_parts
from budget_app.services.context import Context, ValidationError
from budget_app.services.undo import recorded

CURRENCY = BASE_CURRENCY  # the declaration is in CHF


@dataclass
class Entry:
    """One row of a section: a transaction (its matching parts summed), or a dividend."""

    date: date
    payee: str  # the payee, the account paid into, or the holding
    category: str  # the category or categories that matched, as shown
    amount: float  # CHF, signed for the section: a refund in a spending section is negative
    note: str = ""
    tax: float = 0.0  # the securities list: the withholding tax of a dividend, CHF
    transaction_id: str = ""  # the transaction to open; for a dividend its income row
    tags: list[str] = field(default_factory=list)  # the linked tags that put it here
    attachments: list[Attachment] = field(default_factory=list)  # the proof (F12)

    @property
    def label(self) -> str:
        """Who was paid, or the note when there is no payee."""
        return self.payee or self.note


@dataclass
class SectionSummary:
    section: Section
    entries: list[Entry]
    tags: list[str]  # the tag ids linked to the section

    @property
    def total(self) -> float:
        return round(sum(e.amount for e in self.entries), 2)

    @property
    def tax_total(self) -> float:
        return round(sum(e.tax for e in self.entries), 2)

    @property
    def attachments(self) -> int:
        return sum(len(e.attachments) for e in self.entries)


@dataclass
class Summary:
    year: int
    sections: list[SectionSummary]

    @property
    def empty(self) -> bool:
        return not any(s.entries for s in self.sections)

    @property
    def overlapping(self) -> int:
        """How many transactions appear in more than one section."""
        seen: dict[str, int] = defaultdict(int)
        for s in self.sections:
            for e in s.entries:
                if e.transaction_id:
                    seen[e.transaction_id] += 1
        return sum(1 for n in seen.values() if n > 1)

    def section(self, key: str) -> SectionSummary:
        return next(s for s in self.sections if s.section.key == key)


# ------------------------------------------------------------------ the tags linked to sections
def links(ctx: Context) -> dict[str, list[str]]:
    """Section key -> the ids of the tags linked to it that still exist (by name)."""
    known = tag_svc.by_id(ctx)
    rows = {row.id: row for row in ctx.store.tax_sections.all()}
    out: dict[str, list[str]] = {}
    seen: set[str] = set()
    for s in SECTIONS:  # a tag in two sections (after a merge) counts in the first
        row = rows.get(s.key)
        ids = [i for i in tag_codec.split(row.tags) if i in known and i not in seen] if row else []
        seen.update(ids)
        out[s.key] = sorted(ids, key=lambda i: (tag_codec.key(known[i].name), i))
    return out


@recorded(N_("Link tags to tax sections"))
def set_links(ctx: Context, chosen: dict[str, Iterable[str]]) -> dict[str, list[str]]:
    """Link tags to sections: section key -> tag ids, or names of new tags (added as tags). Sections left out keep
    their tags. A tag may belong to one section only; ValidationError names it otherwise. One undoable step."""
    unknown = set(chosen) - set(BY_KEY)
    if unknown:
        raise ValidationError(t("Unknown section"))
    owner: dict[str, str] = {}
    current = links(ctx)
    columns = {}
    for key in BY_KEY:
        column = tag_svc.encode(ctx, chosen[key]) if key in chosen else tag_codec.join(current[key])
        for entry in tag_codec.split(column):
            tag = ctx.store.tags.get(entry)
            name = tag.name if tag else entry.removeprefix(tag_codec.PENDING)
            if tag_codec.key(name) in owner:
                raise ValidationError(t("“{name}” can belong to one section only", name=name))
            owner[tag_codec.key(name)] = key
        columns[key] = column
    for key, column in columns.items():
        stored = tag_svc.store(ctx, column) if key in chosen else column
        row = ctx.store.tax_sections.get(key)
        if row is None:
            if stored:
                ctx.store.tax_sections.add(TaxSection(tags=stored, id=key))
        elif row.tags != stored:
            row.tags = stored
            ctx.store.tax_sections.update(row)
    return links(ctx)


# ------------------------------------------------------------------ years
def default_year(today: date | None = None) -> int:
    """The year the declaration is usually made for: the one before."""
    return (today or date.today()).year - 1


def years(ctx: Context, today: date | None = None) -> list[int]:
    """The years to choose from, newest first: every year with transactions or trades, last year and this one."""
    this = (today or date.today()).year
    found = {tx.date.year for tx in ctx.store.transactions.all()} | {tr.date.year for tr in ctx.store.trades.all()}
    return sorted(found | {this, this - 1}, reverse=True)


# ------------------------------------------------------------------ the summary
def _income_category_ids(ctx: Context) -> set[str]:
    """Income categories whose name is Dividends, Interest or Interest income in any of the app's languages."""
    names = {fold(name) for name in INCOME_NAMES}
    for lang in i18n.LANGUAGES:
        names |= {fold(found) for name in INCOME_NAMES if isinstance(found := i18n.catalog(lang).get(name), str)}
    return {c.id for c in ctx.store.income_categories.all() if fold(c.name.strip()) in names}


def _income_name(ctx: Context, category_id: str) -> str:
    cat = ctx.store.income_categories.get(category_id)
    return cat.name if cat else ""


class _Collector:
    """Gathers the rows of every section for one year."""

    def __init__(self, ctx: Context, year: int) -> None:
        self.ctx = ctx
        self.year = year
        self.links = links(ctx)
        self.tag_section = {tag_id: key for key, ids in self.links.items() for tag_id in ids}
        self.rows: dict[str, list[Entry]] = {s.key: [] for s in SECTIONS}
        self.files: dict[str, list[Attachment]] = defaultdict(list)
        for att in sorted(ctx.store.attachments.all(), key=lambda a: a.added):
            if att.transaction_id:
                self.files[att.transaction_id].append(att)
        self.accounts = {a.id: a for a in ctx.store.accounts.all()}
        self.coicop = get_coicop()

    def _sections_of_tags(self, ids: Iterable[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for tag_id in ids:
            if tag_id in self.tag_section:
                out[self.tag_section[tag_id]].append(tag_id)
        return out

    def _add(self, key: str, tx: Transaction, amount_chf: float, category: str, tag_ids: list[str]) -> None:
        payee = tx.payee
        if not payee and tx.type == TX_TRANSFER:
            acc = self.accounts.get(tx.to_account)
            payee = acc.name if acc else ""
        self.rows[key].append(
            Entry(
                date=tx.date,
                payee=payee,
                category=category,
                amount=round(amount_chf, 2),
                note=tx.comment,
                transaction_id=tx.id,
                tags=list(dict.fromkeys(tag_ids)),
                attachments=list(self.files.get(tx.id, [])),
            )
        )

    def expenditure(self, tx: Transaction, lines) -> None:
        found: dict[str, list] = defaultdict(lambda: [0.0, [], []])  # key -> [CHF, categories, tags]
        for part in transaction_parts(self.ctx, tx, lines):
            if part.category == ADV:  # the share paid for others comes back
                continue
            keys: dict[str, list[str]] = defaultdict(list)
            section = section_of_code(part.category)
            if section is not None:
                keys[section.key] = []
            for key, ids in self._sections_of_tags(part.tag_ids).items():
                keys[key] += ids
            for key, ids in keys.items():
                sign = -1 if BY_KEY[key].income else 1
                found[key][0] += sign * part.amount_chf
                found[key][1].append(part.category)
                found[key][2] += ids
        for key, (amount, codes, ids) in found.items():
            label = ", ".join(self.coicop.label(c) for c in dict.fromkeys(codes))
            self._add(key, tx, amount, label, ids)

    def income(self, tx: Transaction, securities_categories: set[str]) -> None:
        if tx.is_advance:  # a repayment of an advance is no income
            return
        keys = self._sections_of_tags(tag_codec.split(tx.tags))
        if tx.category in securities_categories:
            keys.setdefault(SECURITIES, [])
        for key, ids in keys.items():
            sign = 1 if BY_KEY[key].income else -1
            self._add(key, tx, sign * tx.amount_chf, _income_name(self.ctx, tx.category), ids)

    def transfer(self, tx: Transaction) -> None:
        keys = self._sections_of_tags(tag_codec.split(tx.tags))
        src, dst = self.accounts.get(tx.from_account), self.accounts.get(tx.to_account)
        if dst is not None and dst.asset_class == ASSET_PILLAR3A and (src is None or src.asset_class != ASSET_PILLAR3A):
            keys.setdefault(PILLAR3A, [])
        for key, ids in keys.items():
            self._add(key, tx, tx.amount_chf, t("Account transfer"), ids)

    def dividend(self, trade: Trade, booked: list[Transaction]) -> None:
        holding = self.ctx.store.holdings.get(trade.holding_id)
        gross_tx = next((x for x in booked if x.type == TX_INCOME), None)
        if holding is None or gross_tx is None:  # pragma: no cover - every dividend books its income
            return
        tax_tx = next((x for x in booked if x.type == TX_EXPENDITURE and x.category == TAX), None)
        tax = round(tax_tx.amount_chf, 2) if tax_tx else 0.0
        name = f"{holding.name} ({holding.symbol})" if holding.symbol else holding.name
        files = [a for x in booked for a in self.files.get(x.id, [])]
        self.rows[SECURITIES].append(
            Entry(
                date=trade.date,
                payee=name,
                category=_income_name(self.ctx, gross_tx.category),
                amount=round(gross_tx.amount_chf, 2),
                note=trade.comment,
                tax=tax,
                transaction_id=gross_tx.id,
                attachments=files,
            )
        )
        if tax_tx is not None and trade.tax >= inv.EPS and inv_svc.reclaimable(self.ctx, holding):
            self.rows[WITHHOLDING].append(
                Entry(
                    date=trade.date,
                    payee=name,
                    category=t("Withholding tax"),
                    amount=tax,
                    note=trade.comment,
                    transaction_id=tax_tx.id,
                    attachments=files,
                )
            )


def summary(ctx: Context, year: int) -> Summary:
    """The year's rows and totals per section, in CHF."""
    col = _Collector(ctx, year)
    lines = split_lines(ctx)
    securities_categories = _income_category_ids(ctx)
    dividends = {tr.id: tr for tr in ctx.store.trades.all() if tr.kind == TRADE_DIVIDEND}
    booked: dict[str, list[Transaction]] = defaultdict(list)
    for tx in sorted(ctx.store.transactions.all(), key=lambda x: (x.date, x.id)):
        if tx.invest_id in dividends:
            booked[tx.invest_id].append(tx)
            continue
        if tx.date.year != year:
            continue
        if tx.type == TX_EXPENDITURE:
            col.expenditure(tx, lines.get(tx.id, []))
        elif tx.type == TX_INCOME:
            col.income(tx, securities_categories)
        elif tx.type == TX_TRANSFER:
            col.transfer(tx)
    for trade in sorted(dividends.values(), key=lambda x: (x.date, x.seq)):
        if trade.date.year == year:
            col.dividend(trade, booked.get(trade.id, []))
    sections = []
    for s in SECTIONS:
        rows = sorted(col.rows[s.key], key=lambda e: (e.date, fold(e.label)))
        sections.append(SectionSummary(s, rows, col.links[s.key]))
    return Summary(year, sections)


def code_labels(section: Section) -> str:
    """The categories a section uses by code, as shown in the sheet: "06 Health, 14.2 Health"."""
    coicop = get_coicop()
    return ", ".join(coicop.label(c) for c in section.all_codes)
