"""Faster manual entry (F06): sums in the amount field, templates, recent accounts, duplicates."""

from datetime import date

import pytest

from budget_app.services import templates as templates_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.context import ValidationError
from budget_app.services.transactions import TransactionInput
from budget_app.ui.widgets.amount import evaluate


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("12.50+8.30", 20.8),
        ("1'234.50", 1234.5),
        ("1’234.50", 1234.5),
        ("12,50", 12.5),
        ("(10+5)*2", 30.0),
        ("2×3−1", 5.0),
        ("10÷4", 2.5),
        ("-4+10", 6.0),
        ("+7", 7.0),
        ("4.5", 4.5),
    ],
)
def test_evaluate_sums(text, value):
    assert evaluate(text) == value


@pytest.mark.parametrize("text", [None, "", "12.5+", "3/0", "__import__('os')", "2**8", "a+1", "True", "1,5.2"])
def test_evaluate_refuses_anything_else(text):
    assert evaluate(text) is None


def expense(setup, **kw):
    base = dict(
        date=date(2026, 9, 10),
        type="expenditure",
        amount=4.5,
        currency="CHF",
        category="01.1",
        from_account=setup["chf"].id,
        comment=" Coffee ",
    )
    base.update(kw)
    return TransactionInput(**base)


def test_templates_save_replace_list_and_apply(ctx, setup):
    saved = templates_svc.save(ctx, " Coffee ", expense(setup))
    assert (saved.name, saved.amount, saved.category, saved.comment) == ("Coffee", 4.5, "01.1", "Coffee")
    assert undo_svc.latest(ctx).label == "Save a template"
    again = templates_svc.save(ctx, "coffee", expense(setup, amount=5))  # same name: replaced
    assert again.id == saved.id and [tp.amount for tp in templates_svc.all_templates(ctx)] == [5]
    templates_svc.save(ctx, "Apple", expense(setup, amount=0))
    assert [tp.name for tp in templates_svc.all_templates(ctx)] == ["Apple", "coffee"]

    base = TransactionInput(date=date(2026, 10, 1), currency="EUR")
    applied = templates_svc.as_input(templates_svc.all_templates(ctx)[0], base)
    assert applied.amount is None and applied.currency == "CHF" and applied.date == date(2026, 10, 1)

    templates_svc.delete(ctx, saved.id)
    assert [tp.name for tp in templates_svc.all_templates(ctx)] == ["Apple"]


@pytest.mark.parametrize(
    ("name", "kw", "message"),
    [
        ("", {}, "Name is required"),
        ("FX", {"type": "exchange"}, "can’t be saved as templates"),
        ("Gain", {"type": "revaluation"}, "can’t be saved as templates"),
    ],
)
def test_template_refusals(ctx, setup, name, kw, message):
    with pytest.raises(ValidationError, match=message):
        templates_svc.save(ctx, name, expense(setup, **kw))


def test_recent_accounts_newest_first(ctx, setup):
    assert tx_svc.recent_accounts(ctx) == []
    tx_svc.save(ctx, expense(setup, date=date(2026, 9, 1)))
    tx_svc.save(
        ctx,
        expense(setup, date=date(2026, 9, 20), currency="EUR", from_account=setup["eur"].id),
    )
    assert tx_svc.recent_accounts(ctx) == [setup["eur"].id, setup["chf"].id]


def test_duplicate_input(ctx, setup):
    tx = tx_svc.save(ctx, expense(setup, import_ref="csv:abc"))
    copy = tx_svc.duplicate_input(ctx, tx, date(2026, 10, 19))
    assert (copy.date, copy.import_ref, copy.amount, copy.category) == (date(2026, 10, 19), "", 4.5, "01.1")
    ex = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 1),
            type="exchange",
            amount=90,
            currency="CHF",
            from_account=setup["chf"].id,
            to_account=setup["eur"].id,
            to_amount=93.80,
        ),
    )
    spread = ctx.store.transactions.get(ex.linked_id)
    from_spread = tx_svc.duplicate_input(ctx, spread, date(2026, 10, 19))
    assert from_spread.type == "exchange" and from_spread.to_amount == pytest.approx(93.80)
