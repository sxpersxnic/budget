"""Accounts, advance payment accounts and income categories."""

from __future__ import annotations

from budget_app.domain.models import ACCOUNT_ADVANCE, ACCOUNT_REGULAR, Account, IncomeCategory
from budget_app.services.context import Context, ValidationError

DEFAULT_ADVANCE_NAME = "Advance payments"


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
    for t in ctx.store.transactions.all():
        if account_id in (t.from_account, t.to_account, t.advance_account):
            return True
    return any(e.account_id == account_id for e in ctx.store.account_entries.all())


def _check_name(ctx: Context, name: str, currency: str, kind: str, exclude_id: str = "") -> str:
    name = (name or "").strip()
    if not name:
        raise ValidationError("Name is required")
    if not currency:
        raise ValidationError("Currency is required")
    for a in ctx.store.accounts.all():
        if a.id != exclude_id and a.kind == kind and a.currency == currency and a.name.lower() == name.lower():
            raise ValidationError(f"An account '{name}' in {currency} already exists")
    return name


def add_account(ctx: Context, name: str, currency: str, kind: str = ACCOUNT_REGULAR) -> Account:
    name = _check_name(ctx, name, currency, kind)
    return ctx.store.accounts.add(Account(name=name, currency=currency, kind=kind))


def edit_account(ctx: Context, account_id: str, name: str, currency: str) -> Account:
    acc = ctx.store.accounts.get(account_id)
    if acc is None:
        raise ValidationError("Account not found")
    name = _check_name(ctx, name, currency, acc.kind, exclude_id=acc.id)
    if currency != acc.currency and account_in_use(ctx, acc.id):
        raise ValidationError("The currency cannot be changed while transactions or entries use this account")
    acc.name, acc.currency = name, currency
    return ctx.store.accounts.update(acc)


def delete_account(ctx: Context, account_id: str) -> None:
    if any(account_id in (t.from_account, t.to_account, t.advance_account) for t in ctx.store.transactions.all()):
        raise ValidationError("Accounts with transactions cannot be deleted")
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
    return any(t.type == "income" and t.category == cat_id for t in ctx.store.transactions.all()) or any(
        b.type == "income" and b.category == cat_id for b in ctx.store.budget.all()
    )


def _check_cat_name(ctx: Context, name: str, exclude_id: str = "") -> str:
    name = (name or "").strip()
    if not name:
        raise ValidationError("Name is required")
    if any(c.id != exclude_id and c.name.lower() == name.lower() for c in ctx.store.income_categories.all()):
        raise ValidationError(f"Income category '{name}' already exists")
    return name


def add_income_category(ctx: Context, name: str) -> IncomeCategory:
    return ctx.store.income_categories.add(IncomeCategory(name=_check_cat_name(ctx, name)))


def rename_income_category(ctx: Context, cat_id: str, name: str) -> IncomeCategory:
    cat = ctx.store.income_categories.get(cat_id)
    if cat is None:
        raise ValidationError("Income category not found")
    cat.name = _check_cat_name(ctx, name, exclude_id=cat_id)
    return ctx.store.income_categories.update(cat)


def delete_income_category(ctx: Context, cat_id: str) -> None:
    if income_category_in_use(ctx, cat_id):
        raise ValidationError("Income categories with entries cannot be deleted")
    ctx.store.income_categories.delete([cat_id])
