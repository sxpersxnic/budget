"""Loans and mortgages (F22): the payments due on top (interest and repayment editable, book or skip), then every
loan with its outstanding debt, rate, rhythm and next payment; a loan opens its details (planned payments, what was
booked). The sheet adds and edits a loan. Every save is one undoable step."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from nicegui import ui

from budget_app import i18n
from budget_app.domain import months
from budget_app.domain.fx import FxError
from budget_app.domain.loans import INTEREST_CATEGORY
from budget_app.domain.models import TX_TRANSFER, Loan, Transaction
from budget_app.i18n import t, tn
from budget_app.services import accounts as acc_svc
from budget_app.services import loans as loan_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.loans import Booking, DuePayment, LoanInput, LoanStatus
from budget_app.ui.layout import (
    date_input,
    error,
    fmt,
    frame,
    metric,
    on_data_changed,
    parse_date,
    refresh_due_badge,
    sheet,
    undoable,
)
from budget_app.ui.pages.add_budget import recurrence_label, recurrence_options
from budget_app.ui.widgets.grid import confirm
from budget_app.ui.widgets.selects import (
    account_options,
    currency_select,
    expenditure_category_select,
    payee_input,
    set_options,
)

ROW_ACTION = "flat dense round size=sm"
Refresh = Callable[[], object] | None


def _done(on_saved: Refresh) -> None:
    if on_saved:
        on_saved()


# ------------------------------------------------------------------ texts
def rate_text(rate: float) -> str:
    return f"{rate:g} %"


def terms_line(st: LoanStatus) -> str:
    """ "1.25 % · 2'500.00 CHF every quarter + interest" (or "interest only")."""
    loan, cur = st.loan, st.account.currency
    rhythm = recurrence_label(loan.recur_months).lower()
    if loan.amortization >= loan_svc.EPS:
        repay = t("{amount} {rhythm} + interest", amount=fmt(loan.amortization, cur), rhythm=rhythm)
    else:
        repay = t("interest only, {rhythm}", rhythm=rhythm)
    return " · ".join([rate_text(loan.rate), repay])


def status_line(st: LoanStatus, today: date | None = None) -> str:
    """The next payment, when the loan is paid off, and the end of a fixed term."""
    day = today or date.today()
    if st.paid_off:
        return t("Paid off")
    parts = [t("next on {date}", date=i18n.date_label(st.loan.next_due))]
    if st.payoff is not None:
        parts.append(t("paid off in {month}", month=months.label(months.month_of(st.payoff))))
    if (end := st.loan.fixed_until_date) is not None:
        parts.append(t("fixed until {date}", date=i18n.date_label(end)))
        if end <= day:
            parts.append(t("term ended"))
    return " · ".join(parts)


def source_line(ctx: Context, st: LoanStatus) -> str:
    cash = ctx.store.accounts.get(st.loan.cash_account)
    parts = [t("paid from {account}", account=cash.name) if cash else t("paying account missing")]
    if st.loan.lender:
        parts.append(st.loan.lender)
    if st.loan.comment:
        parts.append(st.loan.comment)
    return " · ".join(parts)


def payment_text(interest: float, amortization: float, currency: str) -> str:
    """ "245.00 CHF interest + 2'500.00 CHF repayment"."""
    parts = []
    if interest >= loan_svc.EPS:
        parts.append(t("{amount} interest", amount=fmt(interest, currency)))
    if amortization >= loan_svc.EPS:
        parts.append(t("{amount} repayment", amount=fmt(amortization, currency)))
    return " + ".join(parts) if parts else fmt(0, currency)


def meter(st: LoanStatus) -> None:
    done = " done" if st.paid_off else ""
    with ui.element("div").classes("meter" + done):
        ui.element("div").style(f"width: {st.share * 100:.1f}%")


# ------------------------------------------------------------------ the loan sheet
def open_loan_sheet(ctx: Context, on_saved: Refresh = None, editing: Loan | None = None) -> None:
    """A new loan with its account, or a loan's terms."""
    ln = editing
    acc = ctx.store.accounts.get(ln.account_id) if ln else None
    init = (
        LoanInput(
            name=acc.name if acc else "",
            currency=acc.currency if acc else "",
            principal=ln.principal,
            start=ln.start,
            rate=ln.rate,
            recur_months=ln.recur_months,
            first_due=ln.first_due,
            amortization=ln.amortization or None,
            cash_account=ln.cash_account,
            interest_category=ln.interest_category,
            lender=ln.lender,
            fixed_until=ln.fixed_until_date,
            comment=ln.comment,
        )
        if ln
        else LoanInput(currency=ctx.store.settings.display_currency, start=date.today(), first_due=date.today())
    )
    touched = {"value": False}
    handled = ln is not None and ln.next_due != ln.first_due
    with sheet(t("Edit Loan") if ln else t("New Loan"), width="34rem") as dlg, ui.column().classes("w-full gap-4"):
        with ui.row().classes("w-full gap-2 items-center no-wrap"):
            name = (
                ui.input(t("Name"), value=init.name, placeholder=t("e.g. Mortgage Bank XY"))
                .props("dense outlined autofocus")
                .classes("flex-1 min-w-0")
            )
            cur = currency_select(ctx, init.currency)
        if acc is not None and acc_svc.account_in_use(ctx, acc.id):
            cur.disable()
            ui.label(t("The currency can’t change while transactions use this loan’s account.")).classes(
                "caption -mt-3 px-1"
            )
        with ui.row().classes("w-full gap-2 no-wrap"):
            principal = (
                ui.number(t("Debt at Start"), value=init.principal, min=0, format="%.2f")
                .props("dense outlined inputmode=decimal")
                .classes("flex-1 min-w-0")
            )
            start = date_input(t("Start Date"), init.start.isoformat() if init.start else "").classes("flex-1 min-w-0")
        ui.label(
            t(
                "The debt owed on the start date. Enter 0 when the payout goes into one of your accounts and record "
                "it as a transfer from the loan."
            )
        ).classes("caption -mt-3 px-1")
        with ui.row().classes("w-full gap-2 no-wrap"):
            rate = (
                ui.number(t("Interest Rate"), value=init.rate, min=0, max=100, precision=4)
                .props('dense outlined inputmode=decimal suffix="% a year"')
                .classes("flex-1 min-w-0")
            )
            recur = (
                ui.select(recurrence_options(), value=init.recur_months, label=t("Repeat"))
                .props("dense outlined options-dense")
                .classes("flex-1 min-w-0")
            )
        with ui.row().classes("w-full gap-2 no-wrap"):
            first_due = date_input(t("First Payment"), init.first_due.isoformat() if init.first_due else "").classes(
                "flex-1 min-w-0"
            )
            amortization = (
                ui.number(t("Repayment per Payment"), value=init.amortization, min=0, format="%.2f")
                .props("dense outlined inputmode=decimal")
                .classes("flex-1 min-w-0")
            )
        ui.label(t("Leave the repayment empty for an interest-only mortgage.")).classes("caption -mt-3 px-1")
        cash = ui.select({}, label=t("Paying Account"), with_input=True).props("dense outlined").classes("w-full")
        category = expenditure_category_select(
            init.interest_category or INTEREST_CATEGORY, label=t("Interest Category")
        )
        ui.label(
            t("Interest is booked in this category; 12.2.1 is what the tax summary counts as debt interest.")
        ).classes("caption -mt-3 px-1")
        lender = payee_input(ctx, init.lender)
        lender.props('label="' + t("Lender") + '"')
        fixed = (
            date_input(t("Fixed Until"), init.fixed_until.isoformat() if init.fixed_until else "")
            .props("clearable")
            .classes("w-full")
        )
        ui.label(t("Optional: when the fixed rate or the term ends (a mortgage renewal).")).classes(
            "caption -mt-3 px-1"
        )
        comment = (
            ui.textarea(t("Note"), value=init.comment, placeholder=t("Optional"))
            .props("dense outlined autogrow rows=1")
            .classes("w-full")
        )
        if handled:
            ui.label(t("Payments already booked stay as they are; the schedule continues on the new terms.")).classes(
                "caption px-1"
            )
        preview = ui.label().classes("caption px-1")
        with ui.row().classes("w-full items-center gap-2 no-wrap pt-1"):
            status = ui.label().classes("caption")
            ui.space()
            ui.button(t("Cancel"), on_click=dlg.close).props("flat").classes("text-primary")
            save_btn = ui.button(t("Save Changes") if ln else t("Add Loan")).props("unelevated")

    def read() -> LoanInput:
        return LoanInput(
            name=name.value or "",
            currency=cur.value or "",
            principal=principal.value,
            start=parse_date(start.value),
            rate=rate.value,
            recur_months=int(recur.value or 1),
            first_due=parse_date(first_due.value),
            amortization=amortization.value,
            cash_account=cash.value or "",
            interest_category=category.value or "",
            lender=lender.value or "",
            fixed_until=parse_date(fixed.value),
            comment=comment.value or "",
        )

    def describe(inp: LoanInput) -> str:
        owed = float(inp.principal or 0)
        first = inp.first_due or date.today()
        repay = float(inp.amortization or 0)
        payments = loan_svc.plan.schedule(owed, float(inp.rate or 0), inp.recur_months, repay, first)
        if not payments:
            return t("No debt yet: the schedule starts once the payout is recorded.")
        p = payments[0]
        text = t(
            "First payment on {date}: {payment}",
            date=i18n.date_label(p.date),
            payment=payment_text(p.interest, p.amortization, inp.currency),
        )
        end = loan_svc.plan.payoff(payments)
        if end is not None:
            text += " · " + t(
                "paid off in {month} after {amount} of interest",
                month=months.label(months.month_of(end)),
                amount=fmt(loan_svc.plan.interest_total(payments), inp.currency),
            )
        else:
            text += " · " + t("interest only")
        return text

    def changed() -> None:
        for el in (principal, amortization):
            el.props(f'suffix="{cur.value or ""}"')
        set_options(cash, account_options(ctx, currency=cur.value, loans=False) if cur.value else {})
        if cash.value is None and len(cash.options) == 1:
            cash.value = next(iter(cash.options))
        inp = read()
        try:
            loan_svc.validate(ctx, inp, ln.id if ln else "")
        except ValidationError as exc:
            save_btn.disable()
            status.set_text(str(exc) if touched["value"] or ln else "")
            preview.set_text("")
            return
        save_btn.enable()
        status.set_text("")
        preview.set_text(describe(inp))

    def save() -> None:
        try:
            saved = loan_svc.edit(ctx, ln.id, read()) if ln else loan_svc.add(ctx, read())
        except (ValidationError, FxError) as exc:
            error(str(exc))
            return
        undoable(t("Changes saved") if ln else t("Added {name}", name=loan_svc.title(ctx, saved)))
        dlg.close()
        _done(on_saved)

    save_btn.on_click(save)
    for el in (name, cur, principal, start, rate, recur, first_due, amortization, cash, category, fixed):
        el.on_value_change(changed)
    changed()
    touched["value"] = True


# ------------------------------------------------------------------ the details sheet
def open_loan_details(ctx: Context, loan_id: str, on_changed: Callable[[], None] | None = None) -> None:
    """A loan's figures, its planned payments and what was booked for it."""
    from budget_app.ui.widgets.transaction_form import open_transaction_sheet  # noqa: PLC0415 - avoids an import cycle

    try:
        st = loan_svc.status(ctx, loan_svc.get(ctx, loan_id))
    except ValidationError as exc:
        error(str(exc))
        return
    cur = st.account.currency
    with sheet(st.account.name, width="36rem") as dlg, ui.column().classes("w-full gap-4"):
        ui.label(" · ".join([terms_line(st), status_line(st)])).classes("caption -mt-3")
        with ui.element("div").classes("metrics w-full").style("--n: 3"):
            metric(t("Outstanding"), st.debt, cur)
            metric(t("Repaid"), st.repaid, cur, sub=f"{st.share * 100:.0f} %" if st.loan.principal else "")
            metric(t("Interest {year}", year=str(date.today().year)), st.interest_year, cur)
        meter(st)
        ui.label(t("Planned Payments")).classes("group-header")
        with ui.element("div").classes("group"):
            if not st.upcoming:
                with ui.element("div").classes("group-row"):
                    ui.label(t("Nothing is planned: the loan is paid off.")).classes("text-2")
            for p in st.upcoming:
                with ui.element("div").classes("group-row"):
                    with ui.column().classes("gap-0 flex-1 min-w-0"):
                        ui.label(i18n.date_label(p.date))
                        ui.label(payment_text(p.interest, p.amortization, cur)).classes("caption")
                    ui.label(t("{amount} left", amount=fmt(p.debt_after, cur))).classes("text-2 num")
            if len(st.upcoming) == loan_svc.plan.PREVIEW and st.payoff not in {p.date for p in st.upcoming}:
                with ui.element("div").classes("group-row"):
                    ui.label(t("… and so on")).classes("caption")
        booked = loan_svc.payments(ctx, st.loan.id)
        ui.label(tn("Booked · {n} transaction", "Booked · {n} transactions", len(booked))).classes("group-header")
        with ui.element("div").classes("group"):
            if not booked:
                with ui.element("div").classes("group-row"):
                    ui.label(t("No payment booked yet.")).classes("text-2")
            for tx in booked:

                def open_tx(tx: Transaction = tx) -> None:
                    open_transaction_sheet(ctx, on_changed, editing=tx)

                _booked_row(ctx, tx, open_tx)
        with ui.row().classes("w-full justify-end gap-2 pt-1"):
            ui.button(t("Close"), on_click=dlg.close).props("flat").classes("text-primary")


def _booked_row(ctx: Context, tx: Transaction, on_click: Callable[[], object]) -> None:
    kind = t("Repayment") if tx.type == TX_TRANSFER else t("Interest")
    with ui.element("div").classes("group-row cursor-pointer").on("click", on_click):
        with ui.column().classes("gap-0 flex-1 min-w-0"):
            ui.label(f"{i18n.date_label(tx.date)} · {kind}")
            if tx.comment or tx.payee:
                ui.label(" · ".join(x for x in (tx.payee, tx.comment) if x)).classes("caption truncate")
        ui.label(fmt(tx.amount, tx.currency)).classes("num")


# ------------------------------------------------------------------ the page
def page() -> None:
    with frame(t("Loans"), "/loans", narrow=True) as (ctx, actions):
        with actions:
            ui.button(t("New Loan"), icon="add", on_click=lambda: open_loan_sheet(ctx, refresh)).props("unelevated")
        ui.label(
            t(
                "Mortgages and loans: the debt counts against your wealth, and when a payment is due it is listed "
                "here with the interest on the outstanding debt and the repayment. Book it, or change the amounts "
                "to what the bank charged."
            )
        ).classes("caption -mt-2")
        inputs: dict[tuple[str, date], tuple[ui.number, ui.number, ui.input]] = {}

        def changes() -> loan_svc.Changes:
            return {key: (i.value, a.value, parse_date(d.value)) for key, (i, a, d) in inputs.items()}

        @ui.refreshable
        def due_list() -> None:
            today = date.today()
            payments = loan_svc.due(ctx, today)
            inputs.clear()
            with ui.column().classes("w-full gap-0"):
                with ui.row().classes("w-full items-end gap-2"):
                    ui.label(t("Due")).classes("group-header")
                    ui.space()
                    if len(payments) > 1:
                        ui.button(t("Book All"), on_click=book_all).props("flat dense no-caps").classes("link-btn")
                with ui.element("div").classes("group"):
                    if not payments:
                        with ui.element("div").classes("group-row"):
                            ui.label(t("Nothing is due.")).classes("text-2")
                    earlier: dict[str, date] = {}
                    for d in payments:
                        _due_row(d, earlier.get(d.loan.id))
                        earlier.setdefault(d.loan.id, d.date)

        def _due_row(d: DuePayment, waits_for: date | None) -> None:
            acc = ctx.store.accounts.get(d.loan.account_id)
            cur = acc.currency if acc else ""
            with ui.element("div").classes("group-row flex-wrap"):
                with ui.column().classes("gap-0 flex-1 " + ("min-w-[10rem]" if waits_for else "w-full")):
                    ui.label(acc.name if acc else "")
                    caption = i18n.date_label(d.date)
                    if waits_for:
                        caption += " · " + t("after the one on {date}", date=i18n.date_label(waits_for))
                    ui.label(caption).classes("caption")
                if waits_for:
                    ui.label(payment_text(d.interest, d.amortization, cur)).classes("text-2")
                    return
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    interest = (
                        ui.number(t("Interest"), value=d.interest, min=0, format="%.2f")
                        .props(f'dense outlined inputmode=decimal suffix="{cur}"')
                        .classes("w-36")
                    )
                    amortization = (
                        ui.number(t("Repayment"), value=d.amortization, min=0, format="%.2f")
                        .props(f'dense outlined inputmode=decimal suffix="{cur}"')
                        .classes("w-36")
                    )
                    when = date_input(t("Date"), d.date.isoformat()).classes("w-40")
                    inputs[(d.loan.id, d.date)] = (interest, amortization, when)
                    with ui.row().classes("gap-0 no-wrap"):
                        ui.button(
                            t("Book"),
                            on_click=lambda: book(d, interest.value, amortization.value, parse_date(when.value)),
                        ).props("flat dense no-caps").classes("text-primary")
                        ui.button(t("Skip"), on_click=lambda: skip(d)).props("flat dense no-caps").classes("text-2")

        @ui.refreshable
        def loan_list() -> None:
            rows = loan_svc.statuses(ctx)
            with ui.column().classes("w-full gap-0"):
                ui.label(tn("{n} loan", "{n} loans", len(rows))).classes("group-header")
                with ui.element("div").classes("group"):
                    if not rows:
                        with ui.element("div").classes("group-row"):
                            ui.label(t("No loans yet.")).classes("text-2")
                    for st in rows:
                        _row(st)

        def _row(st: LoanStatus) -> None:
            loan, cur = st.loan, st.account.currency
            with ui.element("div").classes("group-row items-start py-2.5"):
                with (
                    ui.column()
                    .classes("gap-1 flex-1 min-w-0 cursor-pointer")
                    .on("click", lambda: open_loan_details(ctx, loan.id, refresh))
                ):
                    with ui.row().classes("w-full items-baseline gap-2"):
                        ui.label(st.account.name).classes("flex-1 min-w-[8rem] truncate font-medium")
                        ui.label(t("{amount} outstanding", amount=fmt(st.debt, cur))).classes("goal-amount")
                    meter(st)
                    ui.label(terms_line(st)).classes("caption")
                    ui.label(status_line(st)).classes("caption " + ("text-pos" if st.paid_off else ""))
                    ui.label(source_line(ctx, st)).classes("caption")
                with ui.row().classes("row-actions gap-0 no-wrap"):
                    ui.button(icon="o_edit", on_click=lambda: open_loan_sheet(ctx, refresh, loan)).props(
                        f'{ROW_ACTION} aria-label="{t("Edit")}"'
                    ).classes("text-primary").tooltip(t("Edit"))
                    ui.button(icon="o_delete", on_click=lambda: delete(st)).props(
                        f'{ROW_ACTION} aria-label="{t("Delete")}"'
                    ).classes("text-negative").tooltip(t("Delete"))

        def refresh() -> None:
            due_list.refresh()
            loan_list.refresh()
            refresh_due_badge()

        def report(result: Booking) -> None:
            n = len(result.booked)
            if n:
                undoable(tn("{n} transaction booked", "{n} transactions booked", n))
            for d, reason in result.failed:
                error(f"{loan_svc.title(ctx, d.loan)} · {i18n.date_label(d.date)}: {reason}")
            refresh()

        def book(d: DuePayment, interest: float | None, amortization: float | None, when: date | None) -> None:
            try:
                booked = loan_svc.book(ctx, d.loan.id, d.date, interest, amortization, when)
            except (ValidationError, FxError) as exc:
                error(str(exc))
                refresh()
                return
            report(Booking(booked))

        def skip(d: DuePayment) -> None:
            try:
                loan_svc.skip(ctx, d.loan.id, d.date)
            except ValidationError as exc:
                error(str(exc))
                refresh()
                return
            undoable(t("Skipped the payment of {date}", date=i18n.date_label(d.date)))
            refresh()

        def book_all() -> None:
            try:
                result = loan_svc.book_all(ctx, date.today(), changes())
            except (ValidationError, FxError) as exc:
                error(str(exc))
                refresh()
                return
            report(result)

        def delete(st: LoanStatus) -> None:
            def do() -> None:
                try:
                    loan_svc.delete(ctx, st.loan.id)
                except ValidationError as exc:
                    error(str(exc))
                    return
                undoable(t("Deleted {name}", name=st.account.name))
                refresh()

            confirm(
                t("Delete “{name}”?", name=st.account.name),
                do,
                detail=t("The loan and its account are removed. A loan with transactions can’t be deleted."),
            )

        due_list()
        loan_list()
        on_data_changed(refresh)
