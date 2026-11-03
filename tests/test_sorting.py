"""To sort: imported spending and income without a category, asked about once per payee."""

from datetime import date

import pytest

from budget_app.domain.coicop import TO_SORT, get_coicop
from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME
from budget_app.services import aggregates, reports, sorting, trends, undo
from budget_app.services import rules as rules_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.context import ValidationError
from budget_app.services.transactions import TransactionInput


def imported(setup, payee: str, amount: float, day: int = 2, text: str = "", **kw) -> TransactionInput:
    """A transaction as an import stores it: with its source row's reference, here without a category."""
    type_ = TX_INCOME if amount > 0 else TX_EXPENDITURE
    accounts = {"to_account": setup["chf"].id} if amount > 0 else {"from_account": setup["chf"].id}
    return TransactionInput(
        date=date(2026, 9, day),
        type=type_,
        amount=abs(amount),
        currency="CHF",
        payee=payee,
        comment=text or payee,
        import_ref=f"csv:{payee}{day}{amount}",
        **accounts,
        **kw,
    )


def test_only_an_imported_transaction_may_wait_without_a_category(ctx, setup):
    tx_svc.save(ctx, imported(setup, "Amazon", -45.8))
    tx_svc.save(ctx, imported(setup, "Muster AG", 7800))
    typed = imported(setup, "Kiosk", -5)
    typed.import_ref = ""
    with pytest.raises(ValidationError, match="Select an expenditure category"):
        tx_svc.save(ctx, typed)
    assert [tx.category for tx in sorting.unsorted(ctx)] == [TO_SORT, TO_SORT]
    assert get_coicop().label(TO_SORT) == "To sort"


def test_groups_ask_per_payee_with_the_likeliest_answers(ctx, setup):
    for day in (2, 3, 4):
        tx_svc.save(ctx, imported(setup, "Amazon", -20 - day, day))
    tx_svc.save(ctx, imported(setup, "Sandro M.", -30, text="TWINT *Sandro M."))
    tx_svc.save(ctx, imported(setup, "Bancomat ZKB", -200, text="Bancomat ZKB Bezug"))
    tx_svc.save(ctx, imported(setup, "Muster AG", 7800))
    tx_svc.save(ctx, imported(setup, "Kiosk Frieda", -4, text="Kiosk Frieda"))
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 1),
            type=TX_INCOME,
            amount=100,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
        ),
    )

    groups = {g.name: g for g in sorting.groups(ctx)}
    assert next(iter(groups)) == "Amazon"  # the most transactions first
    amazon = groups["Amazon"]
    assert (amazon.count, amazon.kind, amazon.total) == (3, sorting.SHOP, pytest.approx(69.0))
    assert amazon.choices == ["08.1", "09.7", "05.6", "03.1"]  # a multi-purpose shop's usual answers
    assert groups["Sandro M."].kind == sorting.PERSON and groups["Sandro M."].choices[:2] == [sorting.GIFTS, "11.1"]
    assert groups["Bancomat ZKB"].kind == sorting.ATM
    assert groups["Kiosk Frieda"].choices[0] == "02.3"  # the search word in the name
    assert groups["Muster AG"].kind == sorting.INCOME and groups["Muster AG"].choices == [setup["salary"].id]
    assert sorting.count(ctx) == 5


def test_an_answer_sorts_the_whole_payee_and_can_be_remembered_in_one_step(ctx, setup):
    for day in (2, 3):
        tx_svc.save(ctx, imported(setup, "Amazon", -20, day))
    [amazon] = sorting.groups(ctx)
    assert sorting.answer(ctx, amazon, "08.1", remember=True) == 2
    assert sorting.unsorted(ctx) == []
    [rule] = rules_svc.all_rules(ctx)
    assert (rule.match, rule.type, rule.category) == ("Amazon", TX_EXPENDITURE, "08.1")

    undo.undo(ctx, undo.latest(ctx).id)  # one step: the categories and the rule
    assert len(sorting.unsorted(ctx)) == 2 and rules_svc.all_rules(ctx) == []


def test_spending_to_sort_counts_in_the_totals(ctx, setup):
    tx_svc.save(ctx, imported(setup, "Amazon", -100))
    tx_svc.save(ctx, imported(setup, "Muster AG", 500))
    rep = reports.month_report(ctx, "2026-09", "CHF")
    assert rep.top_expenditure[-1] == TO_SORT
    line = rep.expenditure_tree[TO_SORT]
    assert (line.label, line.actual, line.editable) == ("To sort", pytest.approx(100.0), False)
    assert rep.total_expenditure.actual == pytest.approx(100.0)
    [income] = rep.income_lines
    assert (income.label, income.editable) == ("To sort", False)
    assert aggregates.rollup({TO_SORT: 5.0, "01.1": 2.0}) == {TO_SORT: 5.0, "01.1": 2.0, "01": 2.0}
    assert trends.group_of(TO_SORT) == TO_SORT  # the Overview's notable spending used to fail here

    empty = reports.month_report(ctx, "2026-08", "CHF")
    assert TO_SORT not in empty.top_expenditure  # no empty To sort line
