"""Accounts, advance payment accounts and income categories."""

from __future__ import annotations

from budget_app.domain.models import (
    ACCOUNT_ADVANCE,
    ACCOUNT_REGULAR,
    ASSET_CASH,
    ASSET_CLASSES,
    ASSET_LOAN,
    HOLDING_CLASSES,
    Account,
    IncomeCategory,
)
from budget_app.i18n import N_, t
from budget_app.services import investments as inv_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.undo import recorded

DEFAULT_ADVANCE_NAME = "Advance payments"  # stored account name, matched by value: not translated


def accounts(ctx: Context, kind: str | None = None, currency: str | None = None) -> list[Account]:
    out = [
        a
        for a in ctx.store.accounts.all()
        if (kind is None or a.kind == kind) and (currency is None or a.currency == currency)
    ]
    return sorted(out, key=lambda a: (a.name.lower(), a.currency))


def account_label(acc: Account | None) -> str:
    return f"{acc.name} ({acc.currency})" if acc else ""


def account_in_use(ctx: Context, account_id: str) -> bool:
    for tx in ctx.store.transactions.all():
        if account_id in (tx.from_account, tx.to_account, tx.advance_account):
            return True
    if any(account_id in (r.from_account, r.to_account) for r in ctx.store.recurring.all()):
        return True
    return any(e.account_id == account_id for e in ctx.store.account_entries.all())


def check_name(ctx: Context, name: str, currency: str, kind: str, exclude_id: str = "") -> str:
    name = (name or "").strip()
    if not name:
        raise ValidationError(t("Name is required"))
    if not currency:
        raise ValidationError(t("Currency is required"))
    for a in ctx.store.accounts.all():
        if a.id != exclude_id and a.kind == kind and a.currency == currency and a.name.lower() == name.lower():
            raise ValidationError(t("An account '{name}' in {currency} already exists", name=name, currency=currency))
    return name


def has_holdings(ctx: Context, account_id: str) -> bool:
    return any(h.account_id == account_id for h in ctx.store.holdings.all())


def _check_class(ctx: Context, asset_class: str, kind: str, acc: Account | None = None) -> str:
    """The asset class an account may have (F21): advance-payment accounts are cash. An account becomes a
    securities or crypto account, valued from holdings, right away while nothing is booked or recorded on it,
    and through a conversion otherwise (`investments.convert_account`, decision 13); it stops being one only
    without holdings."""
    if kind == ACCOUNT_ADVANCE:
        return ASSET_CASH
    if asset_class not in ASSET_CLASSES:
        raise ValidationError(t("Select an asset class"))
    if asset_class == ASSET_LOAN and (acc is None or not acc.is_loan):  # F22: only through a loan
        raise ValidationError(t("A loan account is added with its loan on the Loans page"))
    if acc is not None and acc.is_loan and asset_class != ASSET_LOAN:
        raise ValidationError(t("This account belongs to a loan. Delete the loan on the Loans page instead."))
    if acc is None or asset_class == acc.asset_class:
        return asset_class
    if asset_class in HOLDING_CLASSES and acc.asset_class not in HOLDING_CLASSES and account_in_use(ctx, acc.id):
        raise ValidationError(
            t("An account with transactions or balances becomes a securities or crypto account through a conversion")
        )
    if acc.asset_class in HOLDING_CLASSES and asset_class not in HOLDING_CLASSES and has_holdings(ctx, acc.id):
        raise ValidationError(
            t(
                "This account still has holdings, with their trades and prices. It stays a securities or crypto "
                "account until they are deleted on the Investments page."
            )
        )
    return asset_class


def add_loan_account(ctx: Context, name: str, currency: str) -> Account:
    """The liability account of a loan (F22), added by `services.loans` inside the loan's action; Settings can't
    give an account this class."""
    name = check_name(ctx, name, currency, ACCOUNT_REGULAR)
    return ctx.store.accounts.add(Account(name=name, currency=currency, asset_class=ASSET_LOAN))


@recorded(N_("Add an account"))
def add_account(
    ctx: Context, name: str, currency: str, kind: str = ACCOUNT_REGULAR, asset_class: str = ASSET_CASH
) -> Account:
    name = check_name(ctx, name, currency, kind)
    asset_class = _check_class(ctx, asset_class, kind)
    return ctx.store.accounts.add(Account(name=name, currency=currency, kind=kind, asset_class=asset_class))


@recorded(N_("Change an account"))
def edit_account(ctx: Context, account_id: str, name: str, currency: str, asset_class: str | None = None) -> Account:
    acc = ctx.store.accounts.get(account_id)
    if acc is None:
        raise ValidationError(t("Account not found"))
    name = check_name(ctx, name, currency, acc.kind, exclude_id=acc.id)
    if currency != acc.currency and (account_in_use(ctx, acc.id) or has_holdings(ctx, acc.id)):
        raise ValidationError(t("The currency cannot be changed while transactions or entries use this account"))
    acc.asset_class = _check_class(ctx, acc.asset_class if asset_class is None else asset_class, acc.kind, acc)
    acc.name, acc.currency = name, currency
    converted_back = bool(acc.invest_from) and not acc.holds_securities
    if converted_back:  # its recorded balances count again; the conversion's revaluation goes
        acc.invest_from = ""
    saved = ctx.store.accounts.update(acc)
    if converted_back:
        inv_svc.book_conversion(ctx, acc.id)
    return saved


@recorded(N_("Delete an account"))
def delete_account(ctx: Context, account_id: str) -> None:
    if any(account_id in (tx.from_account, tx.to_account, tx.advance_account) for tx in ctx.store.transactions.all()):
        raise ValidationError(t("Accounts with transactions cannot be deleted"))
    if any(account_id in (r.from_account, r.to_account) for r in ctx.store.recurring.all()):
        raise ValidationError(t("Accounts used by recurring transactions cannot be deleted"))
    if has_holdings(ctx, account_id):
        raise ValidationError(t("Accounts with holdings cannot be deleted. Delete the holdings first."))
    if any(loan.account_id == account_id for loan in ctx.store.loans.all()):
        raise ValidationError(t("This account belongs to a loan. Delete the loan on the Loans page instead."))
    ctx.store.account_entries.delete([e.id for e in ctx.store.account_entries.where(account_id=account_id)])
    ctx.store.accounts.delete([account_id])


def default_advance_account(ctx: Context, currency: str) -> Account:
    """The 'Advance payments' account for a currency, created on demand."""
    for a in accounts(ctx, ACCOUNT_ADVANCE, currency):
        if a.name == DEFAULT_ADVANCE_NAME:
            return a
    return ctx.store.accounts.add(Account(name=DEFAULT_ADVANCE_NAME, currency=currency, kind=ACCOUNT_ADVANCE))


# ------------------------------------------------------------- income categories
def income_categories(ctx: Context) -> list[IncomeCategory]:
    return sorted(ctx.store.income_categories.all(), key=lambda c: c.name.lower())


def income_category_in_use(ctx: Context, cat_id: str) -> bool:
    return (
        any(tx.type == "income" and tx.category == cat_id for tx in ctx.store.transactions.all())
        or any(b.type == "income" and b.category == cat_id for b in ctx.store.budget.all())
        or any(r.type == "income" and r.category == cat_id for r in ctx.store.recurring.all())
    )


def _check_cat_name(ctx: Context, name: str, exclude_id: str = "") -> str:
    name = (name or "").strip()
    if not name:
        raise ValidationError(t("Name is required"))
    if any(c.id != exclude_id and c.name.lower() == name.lower() for c in ctx.store.income_categories.all()):
        raise ValidationError(t("Income category '{name}' already exists", name=name))
    return name


@recorded(N_("Add an income category"))
def add_income_category(ctx: Context, name: str) -> IncomeCategory:
    return ctx.store.income_categories.add(IncomeCategory(name=_check_cat_name(ctx, name)))


@recorded(N_("Rename an income category"))
def rename_income_category(ctx: Context, cat_id: str, name: str) -> IncomeCategory:
    cat = ctx.store.income_categories.get(cat_id)
    if cat is None:
        raise ValidationError(t("Income category not found"))
    cat.name = _check_cat_name(ctx, name, exclude_id=cat_id)
    return ctx.store.income_categories.update(cat)


@recorded(N_("Delete an income category"))
def delete_income_category(ctx: Context, cat_id: str) -> None:
    if income_category_in_use(ctx, cat_id):
        raise ValidationError(t("Income categories with entries cannot be deleted"))
    ctx.store.income_categories.delete([cat_id])
