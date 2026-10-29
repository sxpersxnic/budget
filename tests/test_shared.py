"""Who owes what (F23): people from the advance payments, their open items and what they settled this year, the
settle-up plan and its repayments with undo, and the summary as text and PDF."""

from datetime import date

import pytest

from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME
from budget_app.services import shared as shared_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.context import ValidationError
from budget_app.services.transactions import TransactionInput

D = date
TODAY = D(2026, 10, 18)


def advance(setup, day: date, amount: float, share: float, debtor: str, **kw) -> TransactionInput:
    base = dict(
        date=day,
        type=TX_EXPENDITURE,
        amount=amount,
        currency="CHF",
        category="11.1.1",
        from_account=setup["chf"].id,
        is_advance=True,
        advance_mode="amount",
        advance_value=share,
        debtor=debtor,
    )
    return TransactionInput(**(base | kw))


def repay(setup, settled, day: date, amount: float, **kw) -> TransactionInput:
    base = dict(
        date=day,
        type=TX_INCOME,
        amount=amount,
        currency=settled.currency,
        to_account=setup["chf"].id if settled.currency == "CHF" else setup["eur"].id,
        is_advance=True,
        advance_account=settled.advance_account,
        settles_id=settled.id,
    )
    return TransactionInput(**(base | kw))


@pytest.fixture
def debts(ctx, setup):
    """Anna owes 40 CHF (60 less 20 repaid) and 45 EUR; Ben owes 50 CHF; Carla settled everything last year."""
    dinner = tx_svc.save(ctx, advance(setup, D(2026, 9, 12), 120, 60, "Anna", payee="Restaurant X", comment="birthday"))
    tx_svc.save(ctx, repay(setup, dinner, D(2026, 9, 20), 20))
    trip = tx_svc.save(
        ctx,
        advance(setup, D(2026, 10, 1), 90, 45, "anna", currency="EUR", from_account=setup["eur"].id, category="07.3"),
    )
    ben = tx_svc.save(ctx, advance(setup, D(2026, 10, 5), 50, 50, "Ben"))
    old = tx_svc.save(ctx, advance(setup, D(2025, 3, 1), 80, 80, "Carla"))
    tx_svc.save(ctx, repay(setup, old, D(2025, 4, 1), 80))
    return {"dinner": dinner, "trip": trip, "ben": ben, "old": old, **setup}


# ------------------------------------------------------------------ people
def test_people_with_their_open_items(ctx, debts):
    anna, ben, carla = shared_svc.people(ctx, TODAY)
    assert (anna.name, anna.key) == ("anna", "anna")  # the spelling used last
    assert [(i.tx.id, i.outstanding) for i in anna.items] == [(debts["dinner"].id, 40), (debts["trip"].id, 45)]
    assert anna.open == {"CHF": 40, "EUR": 45}
    assert anna.oldest == D(2026, 9, 12)
    assert anna.settled_year == {"CHF": 20}
    assert [i.tx.id for i in anna.open_in("EUR")] == [debts["trip"].id]
    assert (ben.name, ben.open, ben.settled_year) == ("Ben", {"CHF": 50}, {})
    assert (carla.name, carla.items, carla.open, carla.oldest, carla.settled_year) == ("Carla", [], {}, None, {})
    assert shared_svc.people(ctx, D(2025, 12, 31))[-1].settled_year == {"CHF": 80}  # settled that year
    assert shared_svc.person(ctx, "ben", TODAY).name == "Ben"
    with pytest.raises(ValidationError, match="Nobody of that name owes anything"):
        shared_svc.person(ctx, "zoe", TODAY)


def test_advances_without_a_debtor_are_left_out(ctx, setup):
    tx = tx_svc.save(ctx, advance(setup, D(2026, 9, 12), 120, 60, "Anna"))
    tx.debtor = "  "  # the form requires a debtor; an older or edited file may lack one
    ctx.store.transactions.update(tx)
    assert shared_svc.people(ctx, TODAY) == []


def test_repayments_follow_the_debtor_of_their_advance(ctx, debts):
    dinner = ctx.store.transactions.get(debts["dinner"].id)
    dinner.debtor = "Dora"  # renamed after the repayment was recorded: the repayment still names Anna
    ctx.store.transactions.update(dinner)
    by_name = {p.name: p for p in shared_svc.people(ctx, TODAY)}
    assert (by_name["Dora"].open, by_name["Dora"].settled_year) == ({"CHF": 40}, {"CHF": 20})
    assert (by_name["anna"].open, by_name["anna"].settled_year) == ({"EUR": 45}, {})


def test_item_labels(ctx, debts):
    assert shared_svc.item_label(debts["dinner"]) == "Restaurant X · birthday"
    assert shared_svc.item_label(debts["ben"]) == "11.1.1 Restaurants and cafés"
    assert shared_svc.count_label(1) == "1 item" and shared_svc.count_label(3) == "3 items"


# ------------------------------------------------------------------ settling up
def test_the_plan_takes_the_oldest_items_first(ctx, debts):
    anna = shared_svc.person(ctx, "anna", TODAY)
    partial = shared_svc.plan(anna, "CHF", 25)
    assert [(i.tx.id, a) for i, a in partial.parts] == [(debts["dinner"].id, 25)] and partial.total == 25
    tx_svc.save(ctx, advance(debts, D(2026, 10, 10), 30, 30, "Anna"))
    anna = shared_svc.person(ctx, "anna", TODAY)
    both = shared_svc.plan(anna, "CHF", 55)
    assert [a for _, a in both.parts] == [40, 15]
    assert shared_svc.plan(anna, "CHF", 70.004).total == 70  # the cent tolerance of repayments
    with pytest.raises(ValidationError, match=r"Anna owes 70\.00 CHF, not more"):
        shared_svc.plan(anna, "CHF", 70.01)
    with pytest.raises(ValidationError, match="Enter the amount received"):
        shared_svc.plan(anna, "CHF", 0)
    with pytest.raises(ValidationError, match="Enter the amount received"):
        shared_svc.plan(anna, "CHF", None)
    with pytest.raises(ValidationError, match="Anna owes nothing in USD"):
        shared_svc.plan(anna, "USD", 10)


def test_settle_records_one_repayment_per_item(ctx, debts):
    lunch = tx_svc.save(ctx, advance(debts, D(2026, 10, 10), 30, 30, "Anna"))
    booked = shared_svc.settle(ctx, "anna", "CHF", 55, debts["chf"].id, D(2026, 10, 18), "cash at lunch")
    assert [(tx.type, tx.amount, tx.settles_id, tx.is_advance) for tx in booked] == [
        (TX_INCOME, 40, debts["dinner"].id, True),
        (TX_INCOME, 15, lunch.id, True),
    ]
    assert all(tx.date == D(2026, 10, 18) and tx.to_account == debts["chf"].id for tx in booked)
    assert all(tx.payee == "Anna" and tx.comment == "cash at lunch" and tx.debtor == "Anna" for tx in booked)
    assert booked[0].advance_account == debts["dinner"].advance_account
    assert undo_svc.latest(ctx).label == "Settle up"
    anna = shared_svc.person(ctx, "anna", TODAY)
    assert (anna.open, anna.settled_year) == ({"CHF": 15, "EUR": 45}, {"CHF": 75})
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert shared_svc.person(ctx, "anna", TODAY).open == {"CHF": 70, "EUR": 45}


def test_settle_uses_a_default_note_and_checks_the_account(ctx, debts):
    [tx] = shared_svc.settle(ctx, "ben", "CHF", 50, debts["chf"].id)
    assert tx.comment == "Settle-up Ben" and tx.date == date.today()
    assert shared_svc.person(ctx, "ben", TODAY).items == []
    with pytest.raises(ValidationError, match="The To account must be in EUR"):
        shared_svc.settle(ctx, "anna", "EUR", 45, debts["chf"].id)
    assert shared_svc.person(ctx, "anna", TODAY).open == {"CHF": 40, "EUR": 45}  # nothing saved
    with pytest.raises(ValidationError, match="Nobody of that name owes anything"):
        shared_svc.settle(ctx, "zoe", "CHF", 1, debts["chf"].id)


# ------------------------------------------------------------------ the summary
def test_the_summary_as_text(ctx, debts):
    anna = shared_svc.person(ctx, "anna", TODAY)
    assert shared_svc.summary_lines(anna, TODAY) == [
        "Open items for anna · 18 Oct 2026",
        "",
        "12 Sep 2026 · Restaurant X · birthday · 40.00 CHF of 60.00 CHF",
        "1 Oct 2026 · 07.3 Public transport · 45.00 EUR",
        "",
        "Total open: 40.00 CHF",
        "Total open: 45.00 EUR",
        "Settled in 2026: 20.00 CHF",
    ]
    assert shared_svc.summary_text(anna, TODAY).endswith("Settled in 2026: 20.00 CHF\n")
    carla = shared_svc.person(ctx, "carla", D(2025, 12, 31))
    assert shared_svc.summary_lines(carla, D(2025, 12, 31))[2:] == ["Nothing open.", "", "Settled in 2025: 80.00 CHF"]
    assert shared_svc.file_name(anna, "pdf") == "Open items anna.pdf"
    assert shared_svc.file_name(shared_svc.Person("Zoë/Ö*", "z"), "txt") == "Open items ZoëÖ.txt"
    assert shared_svc.file_name(shared_svc.Person("***", "z"), "txt") == "Open items person.txt"


def test_the_summary_as_pdf(ctx, debts):
    anna = shared_svc.person(ctx, "anna", TODAY)
    pdf = shared_svc.summary_pdf(anna, TODAY)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000
    empty = shared_svc.summary_pdf(shared_svc.person(ctx, "carla", TODAY), TODAY)
    assert empty.startswith(b"%PDF")
