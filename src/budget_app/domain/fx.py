"""ECB reference exchange rates.

The ECB publishes EUR-based rates (1 EUR = x CUR). Any conversion between two
currencies is done as a cross rate on the same ECB date:
    amount_to = amount_from / rate[from] * rate[to]
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
import zipfile
from bisect import bisect_left, bisect_right
from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd

from budget_app.config import BASE_CURRENCY, ECB_CSV_NAME, ECB_URL
from budget_app.i18n import N_, t

log = logging.getLogger(__name__)


class FxError(RuntimeError):
    pass


class FxRates:
    def __init__(self, table: pd.DataFrame | None = None):
        """`table`: index = dates (ascending), columns = currency codes, values = units per EUR."""
        self._table = table if table is not None else pd.DataFrame()
        self._series: dict[str, tuple[list[date], list[float]]] = {}
        if not self._table.empty:
            for cur in self._table.columns:
                s = self._table[cur].dropna()
                self._series[cur] = (list(s.index), [float(v) for v in s.values])
            self._dates: list[date] = list(self._table.index)
        else:
            self._dates = []

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_csv(cls, path: Path | str | io.StringIO) -> FxRates:
        df = pd.read_csv(path, na_values=["N/A"], skipinitialspace=True)
        df = df.loc[:, [c for c in df.columns if c and not str(c).startswith("Unnamed")]]
        df["Date"] = pd.to_datetime(df["Date"]).dt.date
        df = df.set_index("Date").sort_index()
        df = df.apply(pd.to_numeric, errors="coerce").dropna(axis=1, how="all")
        df["EUR"] = 1.0
        return cls(df)

    @property
    def available(self) -> bool:
        return bool(self._dates)

    # ------------------------------------------------------------------ queries
    def latest_date(self) -> date | None:
        return self._dates[-1] if self._dates else None

    def currencies(self) -> list[str]:
        """Currencies still quoted on the most recent ECB date (EUR and CHF always included)."""
        if not self.available:
            return [BASE_CURRENCY]
        latest = self._table.iloc[-1]
        return sorted({c for c in latest.index if pd.notna(latest[c])} | {"EUR", BASE_CURRENCY})

    def rate_date_on_or_before(self, d: date) -> date | None:
        """Latest ECB date on or before `d` (falls back to the first date if `d` predates the file)."""
        if not self._dates:
            return None
        i = bisect_right(self._dates, d)
        return self._dates[max(i - 1, 0)]

    def rate(self, currency: str, on: date | None = None) -> float:
        """Units of `currency` per EUR on (or before) the given date."""
        if currency == "EUR":
            return 1.0
        if currency not in self._series:
            raise FxError(t("No ECB rate for currency {currency}", currency=currency))
        dates, values = self._series[currency]
        if on is None:
            return values[-1]
        i = bisect_right(dates, on)
        return values[max(i - 1, 0)]

    def convert(self, amount: float, from_cur: str, to_cur: str, on: date | None = None) -> float:
        if from_cur == to_cur:
            return float(amount)
        return float(amount) / self.rate(from_cur, on) * self.rate(to_cur, on)

    def history(
        self, currency: str, quote: str, start: date, end: date, month_end: bool = False
    ) -> list[tuple[date, float]]:
        """Price of one `currency` in `quote` from `start` to `end` (F38).

        Daily: every ECB date in the range. `month_end`: the last ECB date of each month, the rate
        account balances are valued with. Empty if either currency has no quotes.
        """
        if any(c != "EUR" and c not in self._series for c in (currency, quote)):
            return []
        days: list[date]
        if month_end:
            days = []
            y, m = start.year, start.month
            while date(y, m, 1) <= end:
                last = date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)
                d = self.rate_date_on_or_before(min(last, end))
                # only a date inside the month counts (a month before the file falls back to its first date)
                if d is not None and date(y, m, 1) <= d <= last and (not days or d != days[-1]):
                    days.append(d)
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        else:
            lo, hi = bisect_left(self._dates, start), bisect_right(self._dates, end)
            days = self._dates[lo:hi]
        return [(d, self.convert(1.0, currency, quote, d)) for d in days]


# ---------------------------------------------------------------------- download
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # the zip is ~0.6 MB today
MAX_CSV_BYTES = 100 * 1024 * 1024  # the CSV is ~2 MB today


def download_ecb_csv(target: Path, url: str = ECB_URL, timeout: float = 30.0) -> None:
    """Download the historic ECB zip and atomically replace `target` with the contained CSV."""
    buf = io.BytesIO()
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_bytes():
            buf.write(chunk)
            if buf.tell() > MAX_DOWNLOAD_BYTES:
                raise FxError(t("ECB download is unexpectedly large; aborted"))
    with zipfile.ZipFile(buf) as zf:
        info = next((i for i in zf.infolist() if i.filename.endswith(".csv")), None)
        if info is None:
            raise FxError(t("ECB archive does not contain a CSV file"))
        if info.file_size > MAX_CSV_BYTES:
            raise FxError(t("ECB CSV is unexpectedly large; aborted"))
        content = zf.read(info)  # read into memory only: nothing is extracted to disk by name
    FxRates.from_csv(io.StringIO(content.decode("utf-8")))  # validate before replacing
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".ecb-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)


class FxService:
    """Holds the currently loaded rates and refreshes them once per day."""

    def __init__(self, data_dir: Path):
        self.path = data_dir / "ecb" / ECB_CSV_NAME
        self._refresh_lock = threading.Lock()
        self.rates = FxRates()
        self._error: tuple[str, str] | None = None  # (message, detail), translated when shown
        self._load()

    @property
    def last_error(self) -> str | None:
        """The last loading or download problem in the interface language (it may change after startup)."""
        if self._error is None:
            return None
        message, detail = self._error
        return t(message, error=detail)

    def _load(self) -> None:
        if self.path.exists():
            try:
                self.rates = FxRates.from_csv(self.path)
            except Exception as exc:  # corrupt file: keep empty rates, report error
                log.exception("Could not read ECB file")
                self._error = (N_("Could not read exchange rates: {error}"), str(exc))

    def refresh_if_stale(self, last_download: str | None, today: date | None = None) -> bool:
        """Download the ECB file if it has not yet been downloaded today. Returns True on download."""
        today = today or date.today()
        if last_download == today.isoformat() and self.path.exists():
            return False
        if not self._refresh_lock.acquire(blocking=False):  # a refresh is already running
            return False
        try:
            download_ecb_csv(self.path)
            self._error = None
        except Exception as exc:  # network, HTTP, zip or parse error: keep the old file
            log.warning("ECB download failed: %s", exc)
            self._error = (N_("Exchange rate download failed ({error}). Using the last stored rates."), str(exc))
            return False
        finally:
            self._refresh_lock.release()
        self._load()
        return True
