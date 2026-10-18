"""Creating, editing and deleting transactions, including advance payments."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from budget_app import i18n
from budget_app.domain.coicop import TAX, get_coicop
from budget_app.domain.models import (
    ACCOUNT_ADVANCE,
    ACCOUNT_REGULAR,
    TX_EXCHANGE,
    TX_EXPENDITURE,
    TX_INCOME,
    TX_TRANSFER,
    TX_TYPES,
    Transaction,
)
from budget_app.i18n import N_, t
from budget_app.services import accounts as acc_svc
from budget_app.services.context import Context, ValidationError

EPS = 0.005
FROM, TO, ADVANCE = "from", "to", "advance"
# Whole sentences per account role (word order differs between languages)
_SELECT_ACCOUNT = {
    FROM: N_("Select the From account"),
    TO: N_("Select the To account"),
    ADVANCE: N_("Select the advance payment account"),
}
_ACCOUNT_CURRENCY = {
    FROM: N_("The From account must be in {currency}"),
    TO: N_("The To account must be in {currency}"),
    ADVANCE: N_("The advance payment account must be in {currency}"),
}


@dataclass
class TransactionInput:
    date: date | None = None
    type: str = TX_EXPENDITURE
    amount: float | None = None
    currency: str = ""
    category: str = ""
    from_account: str = ""
    to_account: str = ""
    is_advance: bool = False
    advance_mode: str = "percent"  # "percent" | "amount"
    advance_value: float | None = None  # percentage (0-100) or amount paid by others
    advance_account: str = ""  # empty: default "Advance payments" account
    debtor: str = ""
    settles_id: str = ""
    comment: str = ""
    import_ref: str = ""  # non-empty for imported transactions: valued at the booking date's rate


@dataclass
class OpenAdvance:
    tx: Transaction
    outstanding: float

    @property
    def label(self) -> str:
        tx = self.tx
        return t(
            "{date} · {debtor} · {amount} open",
            date=i18n.date_label(tx.date),
            debtor=tx.debtor or "—",
            amount=f"{self.outstanding:,.2f} {tx.currency}",
        )


def advance_share(inp: TransactionInput) -> float:
    if not inp.is_advance or inp.advance_value is None or inp.amount is None:
        return 0.0
    if inp.advance_mode == "percent":
        return round(inp.amount * inp.advance_value / 100.0, 2)
    return round(inp.advance_value, 2)


# ------------------------------------------------------------------ advances
def settled_amount(ctx: Context, advance_tx_id: str, exclude_id: str = "") -> float:
    return sum(
        tx.amount for tx in ctx.store.transactions.all() if tx.settles_id == advance_tx_id and tx.id != exclude_id
    )


def open_advances(ctx: Context, advance_account_id: str, exclude_id: str = "") -> list[OpenAdvance]:
    """Advanced expenditures on an account that are not yet fully repaid."""
    out = []
    for tx in ctx.store.transactions.all():
        if tx.type == TX_EXPENDITURE and tx.is_advance and tx.advance_account == advance_account_id:
            left = round(tx.advance_amount - settled_amount(ctx, tx.id, exclude_id), 2)
            if left > EPS:
                out.append(OpenAdvance(tx, left))
    return sorted(out, key=lambda o: o.tx.date)


def advance_account_balance(ctx: Context, advance_account_id: str) -> float:
    return round(sum(o.outstanding for o in open_advances(ctx, advance_account_id)), 2)


# ---------------------------------------------------------------- validation
def _account(ctx: Context, account_id: str, role: str, kind: str, currency: str | None):
    acc = ctx.store.accounts.get(account_id) if account_id else None
    if acc is None or acc.kind != kind:
        raise ValidationError(t(_SELECT_ACCOUNT[role]))
    if currency and acc.currency != currency:
        raise ValidationError(t(_ACCOUNT_CURRENCY[role], currency=currency))
    return acc


def validate(ctx: Context, inp: TransactionInput, editing_id: str = "") -> None:
    if inp.date is None:
        raise ValidationError(t("Date is required"))
    if inp.type not in TX_TYPES:
        raise ValidationError(t("Select the type of transaction"))
    if inp.amount is None or inp.amount <= 0:
        raise ValidationError(t("Amount must be a positive number"))
    if not inp.currency:
        raise ValidationError(t("Currency is required"))

    if inp.type == TX_EXPENDITURE:
        if inp.category != TAX and get_coicop().get(inp.category) is None:
            raise ValidationError(t("Select an expenditure category"))
        _account(ctx, inp.from_account, FROM, ACCOUNT_REGULAR, inp.currency)
        if inp.is_advance:
            share = advance_share(inp)
            if share <= 0 or share > inp.amount + EPS:
                raise ValidationError(t("The advance share must be greater than 0 and at most the amount"))
            if inp.advance_account:
                _account(ctx, inp.advance_account, ADVANCE, ACCOUNT_ADVANCE, inp.currency)
            if not inp.debtor.strip():
                raise ValidationError(t("Enter the debtor"))

    elif inp.type == TX_INCOME:
        _account(ctx, inp.to_account, TO, ACCOUNT_REGULAR, inp.currency)
        if inp.is_advance:
            _account(ctx, inp.advance_account, ADVANCE, ACCOUNT_ADVANCE, inp.currency)
            adv = ctx.store.transactions.get(inp.settles_id) if inp.settles_id else None
            if adv is None or adv.advance_account != inp.advance_account:
                raise ValidationError(t("Select the outstanding advance payment that is repaid"))
            left = adv.advance_amount - settled_amount(ctx, adv.id, exclude_id=editing_id)
            if inp.amount > left + EPS:
                raise ValidationError(
                    t(
                        "The repayment exceeds the outstanding amount ({amount})",
                        amount=f"{left:,.2f} {adv.currency}",
                    )
                )
        elif ctx.store.income_categories.get(inp.category) is None:
            raise ValidationError(t("Select an income category"))

    elif inp.type == TX_TRANSFER:
        _account(ctx, inp.from_account, FROM, ACCOUNT_REGULAR, inp.currency)
        _account(ctx, inp.to_account, TO, ACCOUNT_REGULAR, inp.currency)
        if inp.from_account == inp.to_account:
            raise ValidationError(t("From and To account must differ"))

    elif inp.type == TX_EXCHANGE:
        src = _account(ctx, inp.from_account, FROM, ACCOUNT_REGULAR, None)
        dst = _account(ctx, inp.to_account, TO, ACCOUNT_REGULAR, None)
        if src.currency == dst.currency:
            raise ValidationError(t("For a currency exchange the accounts need different currencies"))
        if inp.currency != src.currency:
            raise ValidationError(t("The amount must be entered in the currency of the From account"))


def is_complete(ctx: Context, inp: TransactionInput) -> bool:
    try:
        validate(ctx, inp)
        return True
    except ValidationError:
        return False


# ---------------------------------------------------------------- persistence
def _build(ctx: Context, inp: TransactionInput, base: Transaction | None) -> Transaction:
    if inp.amount is None or inp.date is None:  # guaranteed by validate()
        raise ValidationError(t("Amount and date are required"))
    amount = round(float(inp.amount), 2)
    unchanged = (
        base is not None
        and abs(base.amount - amount) < EPS
        and base.currency == inp.currency
        and (not inp.import_ref or base.date == inp.date)  # an imported row's rate follows its booking date
    )
    if base is not None and unchanged:
        amount_chf, fx_date = base.amount_chf, base.fx_date  # editing other fields keeps the original valuation
    elif inp.import_ref:
        amount_chf, fx_date = ctx.to_base_on(amount, inp.currency, inp.date)
    else:
        amount_chf, fx_date = ctx.to_base_latest(amount, inp.currency)
    tx = Transaction(
        date=inp.date,
        type=inp.type,
        amount=amount,
        currency=inp.currency,
        amount_chf=amount_chf,
        fx_date=fx_date,
        comment=inp.comment.strip(),
        import_ref=inp.import_ref,
    )
    if base is not None:
        tx.id = base.id
    if inp.type == TX_EXPENDITURE:
        tx.category, tx.from_account = inp.category, inp.from_account
        if inp.is_advance:
            tx.is_advance = True
            tx.advance_amount = advance_share(inp)
            tx.advance_account = inp.advance_account or acc_svc.default_advance_account(ctx, inp.currency).id
            tx.debtor = inp.debtor.strip()
    elif inp.type == TX_INCOME:
        tx.to_account = inp.to_account
        if inp.is_advance:
            tx.is_advance, tx.advance_account, tx.settles_id = True, inp.advance_account, inp.settles_id
            settled = ctx.store.transactions.get(inp.settles_id)
            tx.debtor = settled.debtor if settled else ""
        else:
            tx.category = inp.category
    elif inp.type == TX_TRANSFER:
        tx.from_account, tx.to_account = inp.from_account, inp.to_account
    elif inp.type == TX_EXCHANGE:
        dst = ctx.store.accounts.get(inp.to_account)
        if dst is None:
            raise ValidationError(t(_SELECT_ACCOUNT[TO]))
        tx.from_account, tx.to_account, tx.to_currency = inp.from_account, inp.to_account, dst.currency
        tx.to_amount = round(ctx.fx.convert(amount, inp.currency, dst.currency, fx_date), 2)
    return tx


def save(ctx: Context, inp: TransactionInput, editing_id: str = "") -> Transaction:
    validate(ctx, inp, editing_id)
    if editing_id:
        old = ctx.store.transactions.get(editing_id)
        if old is None:
            raise ValidationError(t("Transaction not found"))
        tx = _build(ctx, inp, old)
        repaid = settled_amount(ctx, old.id)
        if repaid > EPS:
            if not tx.is_advance or tx.type != TX_EXPENDITURE or tx.advance_amount + EPS < repaid:
                raise ValidationError(
                    t(
                        "{amount} of this advance has already been repaid; the advance share cannot be lower than that",
                        amount=f"{repaid:,.2f}",
                    )
                )
            if tx.currency != old.currency or tx.advance_account != old.advance_account:
                raise ValidationError(
                    t("This advance already has repayments: its currency and advance payment account cannot change")
                )
        return ctx.store.transactions.update(tx)
    return ctx.store.transactions.add(_build(ctx, inp, None))


def save_many(ctx: Context, inputs: list[TransactionInput]) -> list[Transaction]:
    """Validate every new transaction first, then save them all in one write (used by imports)."""
    for i, inp in enumerate(inputs):
        try:
            validate(ctx, inp)
        except ValidationError as exc:
            raise ValidationError(t("Row {n}: {error}", n=i + 1, error=exc)) from exc
    return ctx.store.transactions.add_many([_build(ctx, inp, None) for inp in inputs])


def to_input(tx: Transaction) -> TransactionInput:
    """Turn a stored transaction back into form input (for editing)."""
    inp = TransactionInput(
        date=tx.date,
        type=tx.type,
        amount=tx.amount,
        currency=tx.currency,
        category=tx.category,
        from_account=tx.from_account,
        to_account=tx.to_account,
        is_advance=tx.is_advance,
        advance_account=tx.advance_account,
        debtor=tx.debtor,
        settles_id=tx.settles_id,
        comment=tx.comment,
        import_ref=tx.import_ref,
    )
    if tx.type == TX_EXPENDITURE and tx.is_advance:
        inp.advance_mode, inp.advance_value = "amount", tx.advance_amount
    return inp


def delete(ctx: Context, ids: list[str]) -> int:
    ids_set = set(ids)
    blockers = [tx for tx in ctx.store.transactions.all() if tx.settles_id in ids_set and tx.id not in ids_set]
    if blockers:
        raise ValidationError(
            t("Some selected advance payments have repayments. Delete the repayments first (or select them as well).")
        )
    return ctx.store.transactions.delete(ids)
