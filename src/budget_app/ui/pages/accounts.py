"""Accounts: the first page of Wealth, with every account's balance and the advance-payment accounts."""

from __future__ import annotations

from budget_app.domain.models import ACCOUNT_ADVANCE, ACCOUNT_REGULAR
from budget_app.i18n import t
from budget_app.ui.layout import frame
from budget_app.ui.pages.settings import accounts_group


def page() -> None:
    with frame(t("Accounts"), "/accounts", narrow=True) as (ctx, _):
        accounts_group(ctx, ACCOUNT_REGULAR, t("Accounts"))
        accounts_group(ctx, ACCOUNT_ADVANCE, t("Advance Payment Accounts"))
