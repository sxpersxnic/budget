"""All persistent tables of the app, bundled in one object."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

from budget_app.domain.models import Account, AccountEntry, BudgetEntry, IncomeCategory, Settings, Transaction
from budget_app.storage.csv_store import CsvTable


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
        self.accounts = CsvTable(data_dir / "accounts.csv", Account)
        self.income_categories = CsvTable(data_dir / "income_categories.csv", IncomeCategory)
        self.transactions = CsvTable(data_dir / "transactions.csv", Transaction)
        self.account_entries = CsvTable(data_dir / "account_entries.csv", AccountEntry)
        self.budget = CsvTable(data_dir / "budget_entries.csv", BudgetEntry)
        self._settings = SettingsStore(data_dir / "settings.json")

    @property
    def settings(self) -> Settings:
        return self._settings.value

    def save_settings(self) -> None:
        self._settings.save()
