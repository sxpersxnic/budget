"""Page for adding a transaction."""

from __future__ import annotations

from budget_app.ui.layout import card, frame
from budget_app.ui.widgets.transaction_form import TransactionForm


def page() -> None:
    with frame("Add transaction", "/add-transaction") as ctx, card():
        TransactionForm(ctx)
