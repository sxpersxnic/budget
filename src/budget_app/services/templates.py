"""Transaction templates (F06): save the usual fields of a transaction under a name, start from them later."""

from __future__ import annotations

from budget_app.domain import tags as tag_codec
from budget_app.domain.models import TX_EXCHANGE, TX_REVALUATION, Template
from budget_app.i18n import N_, t
from budget_app.services import payees as payee_svc
from budget_app.services import tags as tag_svc
from budget_app.services.context import Context, ValidationError
from budget_app.services.transactions import TransactionInput
from budget_app.services.undo import recorded

# exchanges and revaluations depend on amounts that differ every time
NOT_TEMPLATABLE = (TX_EXCHANGE, TX_REVALUATION)


def all_templates(ctx: Context) -> list[Template]:
    return sorted(ctx.store.templates.all(), key=lambda tp: tp.name.lower())


@recorded(N_("Save a template"))
def save(ctx: Context, name: str, inp: TransactionInput) -> Template:
    """Save `inp`'s type, amount, currency, category, accounts, payee, tags and note as `name`.

    A template with the same name is replaced.
    """
    name = (name or "").strip()
    if not name:
        raise ValidationError(t("Name is required"))
    if inp.type in NOT_TEMPLATABLE:
        raise ValidationError(t("Exchanges and revaluations can’t be saved as templates"))
    template = Template(
        name=name,
        type=inp.type,
        amount=round(float(inp.amount or 0), 2),
        currency=inp.currency,
        category=inp.category,
        from_account=inp.from_account,
        to_account=inp.to_account,
        comment=inp.comment.strip(),
        payee=payee_svc.ensure(ctx, inp.payee),
        tags=tag_svc.ensure(ctx, inp.tags),
    )
    same = next((tp for tp in ctx.store.templates.all() if tp.name.lower() == name.lower()), None)
    if same is not None:
        template.id = same.id
        return ctx.store.templates.update(template)
    return ctx.store.templates.add(template)


@recorded(N_("Delete a template"))
def delete(ctx: Context, template_id: str) -> None:
    ctx.store.templates.delete([template_id])


def as_input(template: Template, base: TransactionInput) -> TransactionInput:
    """`base` (today's date, the display currency) with the template's fields filled in."""
    base.type = template.type
    base.amount = template.amount or None
    base.currency = template.currency or base.currency
    base.category = template.category
    base.from_account = template.from_account
    base.to_account = template.to_account
    base.comment = template.comment
    base.payee = template.payee
    base.tags = tag_codec.split(template.tags)
    return base
