"""All persistent tables of the app, bundled in one object."""

from __future__ import annotations

import dataclasses
import json
import os
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
from budget_app.storage.history import History


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path
        self.value = Settings()
        self._unknown: dict = {}  # keys of a newer version, written back unchanged
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            known = {f.name for f in dataclasses.fields(Settings)}
            self.value = Settings(**{k: v for k, v in raw.items() if k in known})
            self._unknown = {k: v for k, v in raw.items() if k not in known}
        else:
            self.save()

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self._unknown | dataclasses.asdict(self.value), f, indent=2)
            f.flush()
            os.fsync(f.fileno())  # never replace the old file with a partially written one
        tmp.replace(self.path)


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        self.history = History(data_dir / "history.jsonl")
        h = self.history
        self.accounts = CsvTable(data_dir / "accounts.csv", Account, h)
        self.income_categories = CsvTable(data_dir / "income_categories.csv", IncomeCategory, h)
        self.transactions = CsvTable(data_dir / "transactions.csv", Transaction, h)
        self.transaction_items = CsvTable(data_dir / "transaction_items.csv", TransactionItem, h)
        self.attachments = CsvTable(data_dir / "attachments.csv", Attachment, h)  # F12; files in attachments/
        self.account_entries = CsvTable(data_dir / "account_entries.csv", AccountEntry, h)
        self.budget = CsvTable(data_dir / "budget_entries.csv", BudgetEntry, h)
        self.templates = CsvTable(data_dir / "templates.csv", Template, h)
        self.recurring = CsvTable(data_dir / "recurring.csv", Recurring, h)
        self.payees = CsvTable(data_dir / "payees.csv", Payee, h)
        self.rules = CsvTable(data_dir / "rules.csv", Rule, h)
        self.holdings = CsvTable(data_dir / "holdings.csv", Holding, h)  # F21
        self.trades = CsvTable(data_dir / "trades.csv", Trade, h)
        self.prices = CsvTable(data_dir / "prices.csv", Price, h)
        self.tags = CsvTable(data_dir / "tags.csv", Tag, h)  # F10
        self.bills = CsvTable(data_dir / "bills.csv", Bill, h)  # F03; their files are attachments
        self.goals = CsvTable(data_dir / "goals.csv", Goal, h)  # F14
        self.scenarios = CsvTable(data_dir / "scenarios.csv", Scenario, h)  # F15
        self.scenario_changes = CsvTable(data_dir / "scenario_changes.csv", ScenarioChange, h)
        self.tax_sections = CsvTable(data_dir / "tax_sections.csv", TaxSection, h)  # F20: tags per section
        # F17: detected subscriptions the user doesn't want listed
        self.subscription_ignores = CsvTable(data_dir / "subscription_ignores.csv", SubscriptionIgnore, h)
        self.loans = CsvTable(data_dir / "loans.csv", Loan, h)  # F22
        self._settings = SettingsStore(data_dir / "settings.json")

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
