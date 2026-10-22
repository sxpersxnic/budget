"""Product list inside the transaction form: name, quantity and unit price per line.

F11: a line of an expenditure can have a category and tags of its own (a split, decision 14). The split button of
a line opens them below it; a line that has them shows them from the start. Under the list, "Rest in 01.1 Food and
non-alcoholic beverages: 23.40" says what stays in the transaction's category. The pickers are built only when a
line opens them, since each category picker carries every category.
"""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from budget_app.domain import splits
from budget_app.domain import tags as tag_codec
from budget_app.domain.coicop import get_coicop
from budget_app.domain.models import TransactionItem
from budget_app.i18n import t, tn
from budget_app.services import items as items_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.items import ItemInput
from budget_app.ui.layout import fmt
from budget_app.ui.widgets.selects import expenditure_category_select
from budget_app.ui.widgets.tags import TagSelect

EPS = 0.005


class _Row:
    """One line: its fields and, once opened, its category and tags."""

    def __init__(self, el: ui.element, name: ui.input, qty: ui.number, price: ui.number, total: ui.label):
        self.el, self.name, self.qty, self.price, self.total = el, name, qty, price, total
        self.split_btn: ui.button | None = None
        self.split_box: ui.element | None = None
        self.category: ui.select | None = None
        self.tags: TagSelect | None = None
        self.init_category = ""  # what the line was loaded with, until its pickers are built
        self.init_tags: list[str] = []

    def read(self) -> ItemInput:
        category = (self.category.value or "") if self.category is not None else self.init_category
        tags = list(self.tags.value or []) if self.tags is not None else list(self.init_tags)
        return ItemInput(self.name.value or "", self.qty.value, self.price.value, category, tags)


class ItemList:
    """Rows of items with a running total compared to the transaction amount.

    `amount` returns the transaction amount; `use_total` sets it to the items' total. `category` returns the
    transaction's category and `splittable` whether it can be split (expenditure only).
    """

    def __init__(
        self,
        known_names: list[str],
        items: list[TransactionItem],
        amount: Callable[[], float | None],
        currency: Callable[[], str],
        use_total: Callable[[float], None],
        ctx: Context | None = None,
        category: Callable[[], str] = str,
        splittable: Callable[[], bool] = lambda: False,
    ):
        self._names = known_names
        self._amount, self._currency, self._use_total = amount, currency, use_total
        self._ctx, self._category, self._splittable = ctx, category, splittable
        self._rows: list[_Row] = []
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
                ui.element("div")
            self._list = ui.column().classes("w-full gap-1")
            self._rest = ui.label().classes("caption px-1")  # F11: what stays in the transaction's category
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
            self.add(item.name, item.quantity, item.unit_price, focus=False, category=item.category, tags=item.tags)
        self.refresh()

    # ------------------------------------------------------------------ rows
    def add(
        self,
        name: str = "",
        quantity: float | None = 1,
        unit_price: float | None = None,
        focus: bool = True,
        category: str = "",
        tags: str = "",
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
            row = _Row(el, name_in, qty, price, line_total)
            row.init_category, row.init_tags = category, tag_codec.split(tags)
            row.split_btn = ui.button(icon="call_split", color=None, on_click=lambda r=row: self._toggle_split(r))
            row.split_btn.props("flat dense round size=sm").classes("text-3 split-btn")
            row.split_btn.tooltip(t("Own category or tags for this item"))
            remove = ui.button(icon="close").props("flat dense round size=sm").classes("text-3")
            remove.tooltip(t("Remove item"))
        self._rows.append(row)
        remove.on_click(lambda r=row: self._remove(r))
        for field in (name_in, qty, price):
            field.on_value_change(self.refresh)
        price.on("keydown.enter", lambda: self.add())  # noqa: PLW0108 - Enter on the price starts the next line
        if category or row.init_tags:
            self._open_split(row)
        if focus:
            name_in.run_method("focus")
        self.refresh()

    def _open_split(self, row: _Row) -> None:
        """Build the line's category and tags pickers below it (once)."""
        if row.split_box is not None:
            row.split_box.set_visibility(True)
            return
        with row.el, ui.element("div").classes("item-split") as box:
            row.category = expenditure_category_select(
                row.init_category or None, on_change=self.refresh, label=t("Category of this item"), ctx=self._ctx
            )
            row.category.props(f'stack-label placeholder="{t("Same as the transaction")}"').classes("min-w-0")
            if self._ctx is not None:
                row.tags = TagSelect(self._ctx, row.init_tags, label=t("Tags of this item"), on_change=self.refresh)
                row.tags.classes("min-w-0")
        row.split_box = box

    def _toggle_split(self, row: _Row) -> None:
        if row.split_box is not None and row.split_box.visible:
            row.split_box.set_visibility(False)
        else:
            self._open_split(row)
        self.refresh()

    def _remove(self, row: _Row) -> None:
        self._rows.remove(row)
        self._list.remove(row.el)
        self.refresh()

    def clear(self) -> None:
        for row in list(self._rows):
            self._remove(row)

    def read(self) -> list[ItemInput]:
        return [r.read() for r in self._rows]

    # ------------------------------------------------------------------ totals
    def refresh(self) -> None:
        inputs = self.read()
        splittable = self._splittable()
        for row, item in zip(self._rows, inputs, strict=True):
            row.total.set_text("" if item.unit_price is None else fmt(item.total))
            self._show_split(row, item, splittable)
        filled = [i for i in inputs if not i.is_blank]
        self._head.set_visibility(bool(self._rows))
        self._use_btn.set_visibility(False)
        self._refresh_rest(filled, splittable)
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

    def _show_split(self, row: _Row, item: ItemInput, splittable: bool) -> None:
        """The split button only for expenditure (kept in its column otherwise); accent while the line is split."""
        if row.split_btn is not None:
            _toggle_class(row.split_btn, "invisible", not splittable)
            marked = bool(item.category or item.tags)
            _toggle_class(row.split_btn, "text-primary", marked)
            _toggle_class(row.split_btn, "text-3", not marked)
        if row.split_box is not None:
            _toggle_class(row.split_box, "hidden", not splittable)

    def _refresh_rest(self, filled: list[ItemInput], splittable: bool) -> None:
        """'Rest in 01.1 Food…: 23.40' while a line has a category of its own; a warning when they exceed the amount.

        Income is not split: if its lines still carry categories or tags (the type was changed), they are dropped
        when it is saved, and the line says so."""
        self._rest.classes(remove="text-negative")
        marked = [i for i in filled if i.category or i.tags]
        if not splittable:
            self._rest.set_text(
                t("Only expenditure is split: the items’ categories and tags are dropped when you save.")
                if marked
                else ""
            )
            return
        category, amount, cur = self._category(), self._amount(), self._currency()
        lines = items_svc.lines(filled)
        if amount is None or amount <= 0 or not any(splits.is_split(line, category) for line in lines):
            self._rest.set_text("")
            return
        try:
            items_svc.check_split(filled, amount, category, cur)
        except ValidationError as exc:
            self._rest.set_text(str(exc))
            self._rest.classes(add="text-negative")
            return
        where = get_coicop().label(category) if category else t("the transaction’s category")
        self._rest.set_text(
            t("Rest in {category}: {amount}", category=where, amount=fmt(splits.rest(amount, lines, category), cur))
        )

    def _apply_total(self) -> None:
        total = items_svc.total([i for i in self.read() if not i.is_blank])
        if total > 0:
            self._use_total(total)
            self.refresh()


def _toggle_class(el: ui.element, name: str, on: bool) -> None:
    if on:
        el.classes(add=name)
    else:
        el.classes(remove=name)
