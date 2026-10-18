"""Page for adding a transaction (the same form also opens as a sheet from the sidebar)."""

from __future__ import annotations

from budget_app.i18n import t
from budget_app.ui.layout import card, frame
from budget_app.ui.widgets.transaction_form import TransactionForm


def page() -> None:
    with frame(t("New Transaction"), "/add-transaction", narrow=True) as (ctx, _), card():
        TransactionForm(ctx)
