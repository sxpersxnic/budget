"""All persistent tables of the app, bundled in one object."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from budget_app.domain.models import (
    Account,
    AccountEntry,
    Attachment,
    Bill,
    BudgetEntry,
    Goal,
    Holding,
    IncomeCategory,
    Loan,
    Payee,
    Price,
    Recurring,
    Rule,
    Scenario,
    ScenarioChange,
    Settings,
    SubscriptionIgnore,
    Tag,
    TaxSection,
    Template,
    Trade,
    Transaction,
    TransactionItem,
)
from budget_app.storage.csv_store import CsvTable
from budget_app.storage.encryption import HISTORY_FILE, PLAIN, Codec, read_text, write_text
from budget_app.storage.history import History


class SettingsStore:
    def __init__(self, path: Path, codec: Codec = PLAIN):
        self.path = path
        self.codec = codec
        self.value = Settings()
        self._unknown: dict = {}  # keys of a newer version, written back unchanged
        if path.exists():
            raw = json.loads(read_text(path, codec))
            known = {f.name for f in dataclasses.fields(Settings)}
            self.value = Settings(**{k: v for k, v in raw.items() if k in known})
            self._unknown = {k: v for k, v in raw.items() if k not in known}
        else:
            self.save()

    def save(self) -> None:
        text = json.dumps(self._unknown | dataclasses.asdict(self.value), indent=2)
        write_text(self.path, text, self.codec)  # fsynced, then renamed into place


class Store:
    """`codec` encodes every record file for the disk (F27): the same object for all tables, so turning
    encryption on sets its key once."""

    def __init__(self, data_dir: Path, codec: Codec | None = None):
        self.data_dir = data_dir
        self.codec = codec if codec is not None else Codec()
        self._open()

    def reload(self) -> None:
        """Read every file again (F26: another computer changed them). The same object, so pages that hold it see
        the new records."""
        self._open()

    def _open(self) -> None:
        data_dir, c = self.data_dir, self.codec
        data_dir.mkdir(parents=True, exist_ok=True)
        self.history = History(data_dir / HISTORY_FILE, codec=c)
        h = self.history
        self.accounts = CsvTable(data_dir / "accounts.csv", Account, h, c)
        self.income_categories = CsvTable(data_dir / "income_categories.csv", IncomeCategory, h, c)
        self.transactions = CsvTable(data_dir / "transactions.csv", Transaction, h, c)
        self.transaction_items = CsvTable(data_dir / "transaction_items.csv", TransactionItem, h, c)
        self.attachments = CsvTable(data_dir / "attachments.csv", Attachment, h, c)  # F12; files in attachments/
        self.account_entries = CsvTable(data_dir / "account_entries.csv", AccountEntry, h, c)
        self.budget = CsvTable(data_dir / "budget_entries.csv", BudgetEntry, h, c)
        self.templates = CsvTable(data_dir / "templates.csv", Template, h, c)
        self.recurring = CsvTable(data_dir / "recurring.csv", Recurring, h, c)
        self.payees = CsvTable(data_dir / "payees.csv", Payee, h, c)
        self.rules = CsvTable(data_dir / "rules.csv", Rule, h, c)
        self.holdings = CsvTable(data_dir / "holdings.csv", Holding, h, c)  # F21
        self.trades = CsvTable(data_dir / "trades.csv", Trade, h, c)
        self.prices = CsvTable(data_dir / "prices.csv", Price, h, c)
        self.tags = CsvTable(data_dir / "tags.csv", Tag, h, c)  # F10
        self.bills = CsvTable(data_dir / "bills.csv", Bill, h, c)  # F03; their files are attachments
        self.goals = CsvTable(data_dir / "goals.csv", Goal, h, c)  # F14
        self.scenarios = CsvTable(data_dir / "scenarios.csv", Scenario, h, c)  # F15
        self.scenario_changes = CsvTable(data_dir / "scenario_changes.csv", ScenarioChange, h, c)
        self.tax_sections = CsvTable(data_dir / "tax_sections.csv", TaxSection, h, c)  # F20: tags per section
        # F17: detected subscriptions the user doesn't want listed
        self.subscription_ignores = CsvTable(data_dir / "subscription_ignores.csv", SubscriptionIgnore, h, c)
        self.loans = CsvTable(data_dir / "loans.csv", Loan, h, c)  # F22
        self._settings = SettingsStore(data_dir / "settings.json", c)

    def tables(self) -> dict[str, CsvTable]:
        """The tables by name, as the history refers to them."""
        tables: list[CsvTable[Any]] = [
            self.accounts,
            self.income_categories,
            self.transactions,
            self.transaction_items,
            self.attachments,
            self.account_entries,
            self.budget,
            self.templates,
            self.recurring,
            self.payees,
            self.rules,
            self.holdings,
            self.trades,
            self.prices,
            self.tags,
            self.bills,
            self.goals,
            self.scenarios,
            self.scenario_changes,
            self.tax_sections,
            self.subscription_ignores,
            self.loans,
        ]
        return {t.name: t for t in tables}

    @property
    def settings(self) -> Settings:
        return self._settings.value

    def save_settings(self) -> None:
        self._settings.save()

    # ------------------------------------------------------------------ small JSON files beside the tables
    def read_json(self, name: str) -> Any:
        """The content of `<data>/<name>` (import memory files), or None when there is no such file."""
        path = self.data_dir / name
        return json.loads(read_text(path, self.codec)) if path.is_file() else None

    def write_json(self, name: str, value: Any) -> None:
        write_text(self.data_dir / name, json.dumps(value, indent=2, sort_keys=True), self.codec)
