from datetime import date

import pandas as pd
import pytest

from budget_app.domain.models import Account
from budget_app.services import export
from budget_app.storage.csv_store import CorruptDataError, CsvTable


def test_round_trip_and_corrupt_value_reports_line(tmp_path):
    table = CsvTable(tmp_path / "accounts.csv", Account)
    table.add(Account(name='Quote "and", comma', currency="CHF", created=date(2026, 1, 2)))
    reloaded = CsvTable(tmp_path / "accounts.csv", Account)
    assert reloaded.all()[0].name == 'Quote "and", comma'
    assert reloaded.all()[0].created == date(2026, 1, 2)

    path = tmp_path / "accounts.csv"
    path.write_text(path.read_text().replace("2026-01-02", "not-a-date"))
    with pytest.raises(CorruptDataError, match="line 2"):
        CsvTable(path, Account)


def test_exports_neutralise_formulas():
    df = pd.DataFrame({"account": ['=HYPERLINK("http://x")', "+1", "Normal"], "change": [-5.0, 1.0, 2.0]})
    csv = export.frame_csv(df).decode()
    assert "'=HYPERLINK" in csv and "'+1" in csv and "Normal" in csv
    assert ",-5.0" in csv  # numbers are untouched
