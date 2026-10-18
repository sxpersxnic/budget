"""Product list inside the transaction form: name, quantity and unit price per line."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from budget_app.domain.models import TransactionItem
from budget_app.i18n import t, tn
from budget_app.services import items as items_svc
from budget_app.services.items import ItemInput
from budget_app.ui.layout import fmt

EPS = 0.005


class ItemList:
    """Rows of items with a running total compared to the transaction amount.

    `amount` returns the transaction amount; `use_total` sets it to the items' total.
    """

    def __init__(
        self,
        known_names: list[str],
        items: list[TransactionItem],
        amount: Callable[[], float | None],
        currency: Callable[[], str],
        use_total: Callable[[float], None],
    ):
        self._names = known_names
        self._amount, self._currency, self._use_total = amount, currency, use_total
        self._rows: list[dict] = []
        with ui.column().classes("w-full gap-1"):
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(t("Items")).classes("text-[13px] font-semibold text-2")
                ui.space()
                self._summary = ui.label().classes("caption")
            self._head = ui.element("div").classes("item-row head")
            with self._head:
                ui.label(t("Item"))
                ui.label(t("Qty")).classes("r")
                ui.label(t("Price")).classes("r")
                ui.label(t("Total")).classes("r c-total")
                ui.element("div")
            self._list = ui.column().classes("w-full gap-1")
            with ui.row().classes("w-full items-center gap-2"):
                add_btn = ui.button(t("Add Item"), icon="add", on_click=lambda: self.add())  # noqa: PLW0108 - the event must not become the name
                add_btn.props("flat dense no-caps").classes("link-btn")
                ui.space()
                self._use_btn = (
                    ui.button(t("Use Total as Amount"), on_click=self._apply_total)
                    .props("flat dense no-caps")
                    .classes("link-btn")
                )
        for item in items:
            self.add(item.name, item.quantity, item.unit_price, focus=False)
        self.refresh()

    # ------------------------------------------------------------------ rows
    def add(
        self, name: str = "", quantity: float | None = 1, unit_price: float | None = None, focus: bool = True
    ) -> None:
        with self._list, ui.element("div").classes("item-row") as el:
            name_in = (
                ui.input(placeholder=t("e.g. Milk"), value=name, autocomplete=self._names)
                .props("dense outlined")
                .classes("min-w-0")
            )
            qty = ui.number(value=quantity, min=0, format="%g").props("dense outlined inputmode=decimal")
            price = ui.number(value=unit_price, format="%.2f").props("dense outlined inputmode=decimal")
            line_total = ui.label().classes("r num text-2 c-total")
            remove = ui.button(icon="close").props("flat dense round size=sm").classes("text-3")
            remove.tooltip(t("Remove item"))
        row = {"el": el, "name": name_in, "qty": qty, "price": price, "total": line_total}
        self._rows.append(row)
        remove.on_click(lambda r=row: self._remove(r))
        for field in (name_in, qty, price):
            field.on_value_change(self.refresh)
        price.on("keydown.enter", lambda: self.add())  # noqa: PLW0108 - Enter on the price starts the next line
        if focus:
            name_in.run_method("focus")
        self.refresh()

    def _remove(self, row: dict) -> None:
        self._rows.remove(row)
        self._list.remove(row["el"])
        self.refresh()

    def clear(self) -> None:
        for row in list(self._rows):
            self._remove(row)

    def read(self) -> list[ItemInput]:
        return [
            ItemInput(name=r["name"].value or "", quantity=r["qty"].value, unit_price=r["price"].value)
            for r in self._rows
        ]

    # ------------------------------------------------------------------ totals
    def refresh(self) -> None:
        inputs = self.read()
        for row, item in zip(self._rows, inputs, strict=True):
            row["total"].set_text("" if item.unit_price is None else fmt(item.total))
        filled = [i for i in inputs if not i.is_blank]
        self._head.set_visibility(bool(self._rows))
        self._use_btn.set_visibility(False)
        if not filled:
            self._summary.set_text("")
            return
        total = items_svc.total(filled)
        amount = self._amount()
        count = tn("{n} item", "{n} items", len(filled))
        if amount is None or amount <= 0:
            self._summary.set_text(t("{count} · {total}", count=count, total=fmt(total, self._currency())))
            self._use_btn.set_visibility(total > 0)
        elif abs(amount - total) < EPS:
            self._summary.set_text(t("{count} · matches the amount", count=count))
        else:
            diff = fmt(abs(amount - total), self._currency())
            self._summary.set_text(
                t("{count} · {difference} not itemised", count=count, difference=diff)
                if amount > total
                else t("{count} · {difference} more than the amount", count=count, difference=diff)
            )
            self._use_btn.set_visibility(total > 0)

    def _apply_total(self) -> None:
        total = items_svc.total([i for i in self.read() if not i.is_blank])
        if total > 0:
            self._use_total(total)
            self.refresh()
