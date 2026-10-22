"""The bulk edit sheet (F13): change the category, payee, account, tags or note of several transactions at once."""

from __future__ import annotations

from collections.abc import Callable

from nicegui import ui
from nicegui.events import KeyEventArguments

from budget_app.domain import tags as tag_codec
from budget_app.domain.fx import FxError
from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME
from budget_app.i18n import t, tn
from budget_app.services import bulk as bulk_svc
from budget_app.services.bulk import BulkChanges, BulkEditError
from budget_app.services.context import Context, ValidationError
from budget_app.ui.layout import error, segmented, sheet, undoable
from budget_app.ui.widgets.selects import (
    account_options,
    expenditure_category_select,
    income_category_select,
    payee_input,
)
from budget_app.ui.widgets.tags import tag_select

NOTE_TEXT = (bulk_svc.NOTE_REPLACE, bulk_svc.NOTE_APPEND)  # the note modes that take a text


def _hint(text: str) -> None:
    ui.label(text).classes("caption -mt-3 px-1")


class BulkEditSheet:
    """One field per kind of change, each empty (left unchanged) at first, and only where it applies.

    A field is built by one method and read in `read()`; its hint says how many rows it leaves as they are.
    """

    def __init__(self, ctx: Context, ids: list[str], on_saved: Callable[[], None] | None = None):
        self.ctx, self.ids, self.on_saved = ctx, ids, on_saved
        self.sel = bulk_svc.selection(ctx, ids)
        self.n = len(self.sel.rows)
        self._unchanged = f'stack-label placeholder="{t("Leave unchanged")}"'  # shown while empty
        with (
            sheet(tn("Edit {n} Transaction", "Edit {n} Transactions", self.n), width="34rem") as self.dlg,
            ui.column().classes("w-full gap-4"),
        ):
            self._category_fields()
            self._payee_field()
            self._account_field()
            self._tag_fields()
            self._note_field()
            self.problems = ui.label().classes("caption text-negative whitespace-pre-line px-1")
            with ui.row().classes("w-full items-center gap-2 no-wrap pt-1"):
                self.status = ui.label().classes("caption")
                ui.space()
                ui.button(t("Cancel"), on_click=self.dlg.close).props("flat").classes("text-primary")
                self.save_btn = ui.button(t("Save"), on_click=self._save).props("unelevated")
            ui.keyboard(on_key=self._shortcut, ignore=[])
        fields = (self.exp_cat, self.inc_cat, self.payee, self.account, self.add_tags, self.remove_tags)
        for el in (*fields, self.note_mode, self.note):
            if el is not None:
                el.on_value_change(self._changed)
        self._changed()

    # ------------------------------------------------------------------ fields
    def _left_out(self, rows: list) -> int:
        """How many selected rows a field leaves as they are."""
        return self.n - len(rows)

    def _category_fields(self) -> None:
        """An expenditure and an income category, each for the rows of its type."""
        expenditure = self.sel.taking(bulk_svc.takes_expenditure_category)
        income = self.sel.taking(bulk_svc.takes_income_category)
        self.exp_cat = self.inc_cat = None
        if expenditure:
            label = t("Expenditure category") if income else t("Category")
            self.exp_cat = expenditure_category_select(label=label, ctx=self.ctx).props(self._unchanged)
            if k := self._left_out(expenditure):
                _hint(
                    tn(
                        "{n} of {total} rows is not expenditure and keeps its category",
                        "{n} of {total} rows are not expenditure and keep their category",
                        k,
                        total=self.n,
                    )
                )
        if income:
            self.inc_cat = income_category_select(self.ctx).props(self._unchanged)
            repayments = any(tx.type == TX_INCOME and tx.is_advance for tx in self.sel.rows)
            if (k := self._left_out(income)) and repayments:
                _hint(
                    tn(
                        "{n} of {total} rows is not income, or repays an advance, and keeps its category",
                        "{n} of {total} rows are not income, or repay an advance, and keep their category",
                        k,
                        total=self.n,
                    )
                )
            elif k:
                _hint(
                    tn(
                        "{n} of {total} rows is not income and keeps its category",
                        "{n} of {total} rows are not income and keep their category",
                        k,
                        total=self.n,
                    )
                )

    def _payee_field(self) -> None:
        """The payee of expenditure and income (F09); a name typed new becomes a payee when saved."""
        rows = self.sel.taking(bulk_svc.takes_payee)
        self.payee = None
        if not rows:
            return
        self.payee = payee_input(self.ctx).props(self._unchanged)
        if k := self._left_out(rows):
            _hint(
                tn(
                    "{n} of {total} rows is a transfer, exchange or revaluation and has no payee",
                    "{n} of {total} rows are transfers, exchanges or revaluations and have no payee",
                    k,
                    total=self.n,
                )
            )

    def _account_field(self) -> None:
        """The From account of expenditure and the To account of income, from the accounts in their currency."""
        rows = self.sel.taking(bulk_svc.takes_account)
        self.account = None
        if not rows:
            ui.label(t("Transfers, exchanges and revaluations keep their accounts.")).classes("caption px-1")
            return
        kinds = {tx.type for tx in rows}
        currencies = self.sel.account_currencies
        label = t("From Account") if kinds == {TX_EXPENDITURE} else t("To Account") if kinds == {TX_INCOME} else None
        self.account = (
            ui.select(
                account_options(self.ctx, currency=currencies[0]) if len(currencies) == 1 else {},
                label=label or t("Account"),
                with_input=True,
                clearable=True,
            )
            .props(f"dense outlined {self._unchanged}")
            .classes("w-full")
        )
        hints = []
        if len(currencies) > 1:
            self.account.disable()
            hints.append(
                t(
                    "The rows are in different currencies ({currencies}), so their account can’t be changed together.",
                    currencies=", ".join(currencies),
                )
            )
        elif label is None:
            hints.append(t("For expenditure the From account, for income the To account."))
        if k := self._left_out(rows):
            hints.append(
                tn(
                    "{n} of {total} rows is a transfer, exchange or revaluation and keeps its accounts.",
                    "{n} of {total} rows are transfers, exchanges or revaluations and keep their accounts.",
                    k,
                    total=self.n,
                )
            )
        if hints:
            _hint(" ".join(hints))

    def _tag_fields(self) -> None:
        """Tags to add to every row (a name typed new becomes a tag when saved) and tags to take off (F10)."""
        rows = self.sel.taking(bulk_svc.takes_tags)
        self.add_tags = self.remove_tags = None
        if not rows:
            return
        with ui.row().classes("w-full gap-2 no-wrap max-sm:flex-wrap"):
            self.add_tags = tag_select(self.ctx, label=t("Add Tags")).props(self._unchanged).classes("flex-1 min-w-0")
            present = {i for tx in rows for i in tag_codec.split(tx.tags)}
            self.remove_tags = tag_select(self.ctx, label=t("Remove Tags"), allow_new=False, only=present)
            self.remove_tags.props(self._unchanged).classes("flex-1 min-w-0")
            if not present:
                self.remove_tags.disable()
        if k := self._left_out(rows):
            _hint(
                tn(
                    "{n} of {total} rows is a spread booking or booked on the Investments page and keeps its tags",
                    "{n} of {total} rows are spread bookings or booked on the Investments page and keep their tags",
                    k,
                    total=self.n,
                )
            )

    def _note_field(self) -> None:
        """Replace, append to or clear the note."""
        with ui.column().classes("w-full gap-2"):
            ui.label(t("Note")).classes("caption px-1")
            self.note_mode = segmented(
                {
                    bulk_svc.NOTE_KEEP: t("Unchanged"),
                    bulk_svc.NOTE_REPLACE: t("Replace"),
                    bulk_svc.NOTE_APPEND: t("Append"),
                    bulk_svc.NOTE_CLEAR: t("Clear"),
                },
                bulk_svc.NOTE_KEEP,
                full=True,
            )
            self.note = ui.textarea(t("Note")).props("dense outlined autogrow rows=1").classes("w-full")
        if invested := self.sel.taking(bulk_svc.is_invested):
            _hint(
                tn(
                    "{n} transaction booked on the Investments page stays as it is; "
                    "it changes with its trade or price.",
                    "{n} transactions booked on the Investments page stay as they are; "
                    "they change with their trade or price.",
                    len(invested),
                )
            )
        if spreads := self.sel.taking(bulk_svc.is_spread):
            _hint(
                tn(
                    "{n} spread booking of a currency exchange keeps its note; it changes with its exchange.",
                    "{n} spread bookings of currency exchanges keep their note; they change with their exchange.",
                    len(spreads),
                )
            )

    def read(self) -> BulkChanges:
        mode = self.note_mode.value
        account = self.account
        return BulkChanges(
            expenditure_category=(self.exp_cat.value or None) if self.exp_cat else None,
            income_category=(self.inc_cat.value or None) if self.inc_cat else None,
            payee=((self.payee.value or "").strip() or None) if self.payee else None,
            account=(account.value or None) if account and account.enabled else None,
            add_tags=list(self.add_tags.value or []) if self.add_tags else [],
            remove_tags=list(self.remove_tags.value or []) if self.remove_tags else [],
            note_mode=mode,
            note=(self.note.value or "") if mode in NOTE_TEXT else "",
        )

    # ------------------------------------------------------------------ events
    def _changed(self) -> None:
        self.note.set_visibility(self.note_mode.value in NOTE_TEXT)
        for tags in (self.add_tags, self.remove_tags):  # "Leave unchanged" only while no tag is chosen
            if tags is not None:
                tags.props(remove="placeholder") if tags.value else tags.props(self._unchanged)
        changes = self.read()
        planned = bulk_svc.plan(self.ctx, self.ids, changes)
        self.save_btn.set_enabled(bool(planned.edits) and not planned.failures)
        self.problems.set_text(str(BulkEditError(planned.failures)) if planned.failures else "")
        if planned.failures:
            self.status.set_text("")
        elif planned.edits:
            k = len(planned.edits)
            self.status.set_text(tn("Changes {n} transaction", "Changes {n} transactions", k))
        elif changes != BulkChanges():
            self.status.set_text(t("Nothing changes: the rows already look like this."))
        else:
            self.status.set_text("")

    def _shortcut(self, e: KeyEventArguments) -> None:
        """⌘↩ / Ctrl+Enter saves, as in the transaction form."""
        if e.action.keydown and e.key.enter and (e.modifiers.meta or e.modifiers.ctrl) and self.save_btn.enabled:
            self._save()

    def _save(self) -> None:
        try:
            done = bulk_svc.bulk_edit(self.ctx, self.ids, self.read())
        except BulkEditError as exc:  # a row changed elsewhere since the sheet checked it
            self.problems.set_text(str(exc))
            self.save_btn.disable()
            error(str(exc).splitlines()[0].rstrip(" :\u00a0"))
            return
        except (ValidationError, FxError) as exc:
            error(str(exc))
            return
        self.dlg.close()
        undoable(tn("Changed {n} transaction", "Changed {n} transactions", len(done)))
        if self.on_saved:
            self.on_saved()


def open_bulk_edit_sheet(ctx: Context, ids: list[str], on_saved: Callable[[], None] | None = None) -> BulkEditSheet:
    """Edit several transactions in one step; each field starts as "leave unchanged"."""
    return BulkEditSheet(ctx, ids, on_saved)
