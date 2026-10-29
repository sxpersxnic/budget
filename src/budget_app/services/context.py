"""Runtime context shared by all services: persistent store + exchange rates."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from budget_app.config import BASE_CURRENCY, app_version
from budget_app.domain import months
from budget_app.domain.fx import FxRates, FxService
from budget_app.storage import backup
from budget_app.storage.encryption import Codec
from budget_app.storage.store import Store


class ValidationError(ValueError):
    """Raised by services when user input violates a business rule."""


@dataclass
class Context:
    store: Store
    fx_service: FxService | None = None
    _fx: FxRates | None = None  # used in tests instead of an FxService

    @property
    def fx(self) -> FxRates:
        if self._fx is not None:
            return self._fx
        return self.fx_service.rates if self.fx_service else FxRates()

    # --------------------------------------------------------------- conversions
    def to_base_latest(self, amount: float, currency: str) -> tuple[float, date]:
        """Convert to CHF with the most recent ECB rate; returns (amount_chf, fx_date)."""
        fx = self.fx
        fx_date = fx.latest_date() or date.today()
        if currency == BASE_CURRENCY:
            return round(float(amount), 6), fx_date
        return round(fx.convert(amount, currency, BASE_CURRENCY, fx_date), 6), fx_date

    def to_base_on(self, amount: float, currency: str, booking: date) -> tuple[float, date]:
        """Convert to CHF with the ECB rate of the booking date (the last rate on or before it)."""
        fx = self.fx
        fx_date = fx.rate_date_on_or_before(booking) or booking
        if currency == BASE_CURRENCY:
            return round(float(amount), 6), fx_date
        return round(fx.convert(amount, currency, BASE_CURRENCY, fx_date), 6), fx_date

    def month_rate_date(self, month: str) -> date:
        """ECB date used to value account balances of a month: the last rate within that month."""
        d = self.fx.rate_date_on_or_before(months.last_day(month))
        return d or date.today()

    def convert_on(self, amount: float, from_cur: str, to_cur: str, on: date | None) -> float:
        return self.fx.convert(amount, from_cur, to_cur, on)

    def horizon(self) -> str:
        """Last budgeted month (defaults to the end of next year)."""
        h = self.store.settings.budget_horizon
        if not h:
            h = f"{date.today().year + 1}-12"
        return h


_ctx: Context | None = None


def init_context(data_dir: Path, codec: Codec | None = None) -> Context:
    """Open the data folder: the version backup, then the store (through `codec` when the records are encrypted, F27)
    and the rates. Called at startup, after the unlock of an encrypted folder, and when the profile changes (F34)."""
    global _ctx  # noqa: PLW0603 - process-wide singleton: the app has one open data folder
    backup.backup_on_version_change(data_dir, app_version())  # before the store reads or writes anything
    _ctx = Context(store=Store(data_dir, codec), fx_service=FxService(data_dir))
    return _ctx


def close_context() -> None:
    global _ctx  # noqa: PLW0603 - see init_context
    _ctx = None


def is_open() -> bool:
    """Whether a data folder is open (an encrypted one is not until it is unlocked, F27)."""
    return _ctx is not None


def get_ctx() -> Context:
    if _ctx is None:
        raise RuntimeError("Context not initialised")
    return _ctx
