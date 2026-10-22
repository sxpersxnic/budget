"""Tags (F10): the tags column, new names that become tags only when stored, rename, recolour, merge, delete,
totals and budgets per tag, templates, recurring series and bulk edit."""

import json
from datetime import date

import pytest

from budget_app.domain import tags as codec
from budget_app.domain.models import Tag, Transaction
from budget_app.services import accounts as acc_svc
from budget_app.services import bulk as bulk_svc
from budget_app.services import export as export_svc
from budget_app.services import recurring as rec_svc
from budget_app.services import tags as tag_svc
from budget_app.services import templates as tpl_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.bulk import BulkChanges
from budget_app.services.context import ValidationError
from budget_app.services.recurring import RecurringInput
from budget_app.services.transactions import TransactionInput
from budget_app.storage.store import Store


def spend(setup, amount=20.0, tags=(), **kw) -> TransactionInput:
    base = dict(
        date=date(2026, 9, 10),
        type="expenditure",
        amount=amount,
        currency="CHF",
        category="01.1",
        from_account=setup["chf"].id,
        tags=list(tags),
    )
    return TransactionInput(**(base | kw))


def earn(setup, amount=100.0, tags=(), **kw) -> TransactionInput:
    base = dict(
        date=date(2026, 9, 25),
        type="income",
        amount=amount,
        currency="CHF",
        category=setup["salary"].id,
        to_account=setup["chf"].id,
        tags=list(tags),
    )
    return TransactionInput(**(base | kw))


def names(ctx, column: str) -> list[str]:
    return [g.name for g in tag_svc.tags_of(ctx, column)]


# ------------------------------------------------------------------ the column
def test_the_tags_column():
    assert codec.split("") == [] and codec.split("a|b||c") == ["a", "b", "c"]
    assert codec.join(["b", "a", "b", ""]) == "b|a"
    assert codec.clean_name("  Ferien |  Italien ") == "Ferien Italien"
    assert codec.key("Ferien Itàlia") == codec.key("ferien   italia")


def test_a_blank_name_finds_no_tag(ctx):
    tag_svc.add(ctx, "Trip")
    assert tag_svc.find(ctx, " | ") is None


def test_old_files_load_without_tags(ctx, setup, tmp_path):
    tx = tx_svc.save(ctx, spend(setup))
    path = tmp_path / "transactions.csv"
    lines = path.read_text().splitlines()
    header = lines[0].split(",")
    i = header.index("tags")
    old = [",".join(c for k, c in enumerate(line.split(",")) if k != i) for line in lines]
    path.write_text("\n".join(old) + "\n")
    store = Store(tmp_path)
    assert store.transactions.get(tx.id).tags == ""
    assert "tags" in store.tables() and store.tags.all() == []


# ------------------------------------------------------------------ new names become tags when stored
def test_a_new_name_becomes_a_tag_only_when_the_transaction_is_stored(ctx, setup):
    holiday = tag_svc.add(ctx, "Holiday Italy", "green")
    inp = spend(setup, tags=[holiday.id, "Renovation", "renovation ", "holiday italy", ""])
    tx_svc.validate(ctx, inp)
    assert tag_svc.encode(ctx, inp.tags) == f"{holiday.id}|+Renovation"
    assert [g.name for g in tag_svc.all_tags(ctx)] == ["Holiday Italy"]  # checking wrote nothing

    tx = tx_svc.save(ctx, inp)
    renovation = tag_svc.find(ctx, "RENOVATION")
    assert renovation is not None and renovation.colour == "grey"
    assert tx.tags == f"{holiday.id}|{renovation.id}"
    assert names(ctx, tx.tags) == ["Holiday Italy", "Renovation"]
    assert tx_svc.to_input(tx).tags == [holiday.id, renovation.id]

    # the save is one undo step, the new tag included
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert ctx.store.transactions.get(tx.id) is None
    assert tag_svc.find(ctx, "Renovation") is None


def test_a_reference_to_a_tag_that_is_gone_is_dropped(ctx, setup):
    tx = tx_svc.save(ctx, spend(setup, tags=["Gone"]))
    ctx.store.tags.delete([codec.split(tx.tags)[0]])  # e.g. undone elsewhere
    assert tag_svc.encode(ctx, tx_svc.edit_input(ctx, tx).tags) == ""
    assert names(ctx, tx.tags) == []
    saved = tx_svc.save(ctx, tx_svc.edit_input(ctx, tx), tx.id)
    assert saved.tags == "" and tag_svc.all_tags(ctx) == []


def test_edit_duplicate_and_import_carry_tags(ctx, setup):
    tx = tx_svc.save(ctx, spend(setup, tags=["Trip"]))
    trip = tag_svc.find(ctx, "Trip")
    edited = tx_svc.save(ctx, tx_svc.edit_input(ctx, tx), tx.id)
    assert edited.tags == trip.id
    assert tx_svc.duplicate_input(ctx, tx, date(2026, 10, 1)).tags == [trip.id]
    rows = tx_svc.save_many(ctx, [spend(setup, tags=["Imported", "Trip"]), spend(setup, tags=["imported"])])
    imported = tag_svc.find(ctx, "Imported")
    assert [r.tags for r in rows] == [f"{imported.id}|{trip.id}", imported.id]


def test_an_exchange_keeps_its_tags_and_its_spread_has_none(ctx, setup):
    tx = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 16),
            type="exchange",
            amount=100,
            currency="CHF",
            from_account=setup["chf"].id,
            to_account=setup["eur"].id,
            to_amount=110.5,
            tags=["Trip"],
        ),
    )
    assert names(ctx, tx.tags) == ["Trip"]
    assert ctx.store.transactions.get(tx.linked_id).tags == ""


# ------------------------------------------------------------------ templates and recurring series
def test_templates_carry_tags(ctx, setup):
    tpl = tpl_svc.save(ctx, "Coffee", spend(setup, 4.5, tags=["Work"]))
    work = tag_svc.find(ctx, "Work")
    assert tpl.tags == work.id
    assert tpl_svc.as_input(tpl, TransactionInput()).tags == [work.id]


def test_recurring_series_carry_tags_to_their_bookings(ctx, setup):
    series = rec_svc.save(
        ctx,
        RecurringInput(
            type="expenditure",
            amount=49.9,
            currency="CHF",
            category="08.3",
            from_account=setup["chf"].id,
            start=date(2026, 9, 15),
            tags=["Phone"],
        ),
    )
    phone = tag_svc.find(ctx, "Phone")
    assert series.tags == phone.id
    assert rec_svc.to_input(series).tags == [phone.id]
    tx = rec_svc.book(ctx, series.id, date(2026, 9, 15))
    assert tx.tags == phone.id
    assert rec_svc.from_transaction(tx).tags == [phone.id]


# ------------------------------------------------------------------ add, rename, recolour, merge, delete
def test_add_rename_and_recolour(ctx, setup):
    trip = tag_svc.add(ctx, "  Trip  ", "blue")
    assert (trip.name, trip.colour) == ("Trip", "blue")
    with pytest.raises(ValidationError, match="already exists"):
        tag_svc.add(ctx, "trip")
    with pytest.raises(ValidationError, match="Name is required"):
        tag_svc.add(ctx, "  ")
    with pytest.raises(ValidationError, match="colours"):
        tag_svc.add(ctx, "Work", "gold")

    tx = tx_svc.save(ctx, spend(setup, tags=[trip.id]))
    renamed = tag_svc.rename(ctx, trip.id, "Holiday Italy")
    assert renamed.name == "Holiday Italy"
    assert ctx.store.transactions.get(tx.id).tags == trip.id  # the records name it by id
    assert tag_svc.rename(ctx, trip.id, "holiday italy").name == "holiday italy"  # its own name, other spelling
    tag_svc.add(ctx, "Work")
    with pytest.raises(ValidationError, match="already exists"):
        tag_svc.rename(ctx, trip.id, "WORK")
    with pytest.raises(ValidationError, match="Tag not found"):
        tag_svc.rename(ctx, "nope", "x")
    assert tag_svc.recolour(ctx, trip.id, "pink").colour == "pink"
    with pytest.raises(ValidationError):
        tag_svc.recolour(ctx, trip.id, "")
    assert tag_svc.options(ctx) == {trip.id: "holiday italy", tag_svc.find(ctx, "Work").id: "Work"}


def test_unknown_colours_show_as_grey():
    assert tag_svc.colour(Tag("x", colour="gold")) == "grey"
    assert tag_svc.colour(Tag("x", colour="teal")) == "teal"


def test_merge_moves_records_to_the_tag_kept(ctx, setup):
    a, b, c = (tag_svc.add(ctx, n) for n in ("Italy", "Italia", "Rome"))
    both = tx_svc.save(ctx, spend(setup, tags=[a.id, b.id, c.id]))
    only_b = tx_svc.save(ctx, spend(setup, tags=[b.id]))
    tpl = tpl_svc.save(ctx, "Pizza", spend(setup, tags=[b.id]))
    kept = tag_svc.merge(ctx, [a.id, b.id, b.id], a.id)
    assert kept.id == a.id
    assert ctx.store.transactions.get(both.id).tags == f"{a.id}|{c.id}"
    assert ctx.store.transactions.get(only_b.id).tags == a.id
    assert ctx.store.templates.get(tpl.id).tags == a.id
    assert [g.name for g in tag_svc.all_tags(ctx)] == ["Italy", "Rome"]
    with pytest.raises(ValidationError, match="at least two"):
        tag_svc.merge(ctx, [a.id], a.id)

    undo_svc.undo(ctx, undo_svc.latest(ctx).id)  # one step
    assert ctx.store.transactions.get(only_b.id).tags == b.id
    assert len(tag_svc.all_tags(ctx)) == 3


def test_delete_takes_the_tag_off_every_record_in_one_step(ctx, setup):
    trip, work = tag_svc.add(ctx, "Trip"), tag_svc.add(ctx, "Work")
    tx = tx_svc.save(ctx, spend(setup, tags=[trip.id, work.id]))
    other = tx_svc.save(ctx, spend(setup))
    tpl = tpl_svc.save(ctx, "Train", spend(setup, tags=[trip.id]))
    series = rec_svc.save(
        ctx,
        RecurringInput(
            type="expenditure",
            amount=10,
            currency="CHF",
            category="01.1",
            from_account=setup["chf"].id,
            start=date(2026, 9, 1),
            tags=[trip.id],
        ),
    )
    assert tag_svc.delete(ctx, [trip.id]) == 1
    assert ctx.store.transactions.get(tx.id).tags == work.id
    assert ctx.store.transactions.get(other.id).tags == ""
    assert ctx.store.templates.get(tpl.id).tags == ""
    assert ctx.store.recurring.get(series.id).tags == ""
    assert tag_svc.find(ctx, "Trip") is None
    with pytest.raises(ValidationError, match="Tag not found"):
        tag_svc.delete(ctx, [trip.id])

    action = undo_svc.latest(ctx)
    assert undo_svc.label(ctx, action) == "Delete tags"
    lines = [undo_svc.describe_change(ctx, c) for c in action.changes]
    assert "Tag deleted: Trip" in lines
    undo_svc.undo(ctx, action.id)
    assert ctx.store.transactions.get(tx.id).tags == f"{trip.id}|{work.id}"
    assert tag_svc.find(ctx, "Trip") is not None


# ------------------------------------------------------------------ totals and budgets
def test_totals_count_the_own_share_and_income(ctx, setup):
    trip = tag_svc.add(ctx, "Trip")
    tx_svc.save(ctx, spend(setup, 100, tags=[trip.id]))
    advance = tx_svc.save(
        ctx,
        spend(setup, 90, tags=[trip.id], is_advance=True, advance_mode="amount", advance_value=60, debtor="Sam"),
    )
    tx_svc.save(
        ctx,
        earn(
            setup,
            60,
            tags=[trip.id],
            category="",
            is_advance=True,
            advance_account=advance.advance_account,
            settles_id=advance.id,
        ),
    )
    tx_svc.save(ctx, earn(setup, 250, tags=[trip.id]))
    tx_svc.save(ctx, spend(setup, 40, tags=[trip.id], currency="EUR", from_account=setup["eur"].id))
    savings = acc_svc.add_account(ctx, "Savings", "CHF")
    transfer = dict(type="transfer", amount=500, from_account=setup["chf"].id, to_account=savings.id, category="")
    tx_svc.save(ctx, spend(setup, date=date(2026, 9, 1), tags=[trip.id], **transfer))
    tx_svc.save(ctx, spend(setup, 999, date=date(2026, 1, 5), tags=[trip.id]))

    total = tag_svc.totals(ctx, "CHF", date(2026, 9, 1), date(2026, 9, 30))[trip.id]
    eur_chf = ctx.convert_on(40, "EUR", "CHF", date(2026, 9, 10))
    assert total.count == 6  # the transfer counts as a transaction, but moves no money in or out
    assert total.spent == pytest.approx(100 + 30 + eur_chf)  # own share only
    assert total.received == pytest.approx(250)  # the repayment comes back an advance, not income
    assert total.last == date(2026, 9, 25)
    assert tag_svc.totals(ctx, "CHF")[trip.id].count == 7
    assert tag_svc.totals(ctx, "CHF", end=date(2026, 8, 31))[trip.id].spent == pytest.approx(999)


def test_budget_status_compares_spending_in_the_period(ctx, setup):
    trip = tag_svc.add(ctx, "Trip")
    assert tag_svc.budget_status(ctx, trip, "CHF") is None
    tx_svc.save(ctx, spend(setup, 300, tags=[trip.id], date=date(2026, 7, 31)))
    tx_svc.save(ctx, spend(setup, 400, tags=[trip.id], date=date(2026, 8, 1)))
    tx_svc.save(ctx, earn(setup, 1000, tags=[trip.id], date=date(2026, 8, 2)))  # income doesn't count
    tx_svc.save(ctx, spend(setup, 50, tags=[trip.id], date=date(2026, 9, 1)))

    trip = tag_svc.set_budget(ctx, trip.id, 500, "CHF", date(2026, 8, 1), date(2026, 8, 31))
    assert (trip.budget, trip.budget_start, trip.budget_end) == (500, "2026-08-01", "2026-08-31")
    status = tag_svc.budget_status(ctx, trip, "CHF")
    assert (status.budget, status.actual, status.start, status.end) == (500, 400, date(2026, 8, 1), date(2026, 8, 31))
    assert (status.remaining, status.ratio, status.over) == (100, 0.8, False)

    trip = tag_svc.set_budget(ctx, trip.id, 600, "CHF", start=date(2026, 7, 1))  # open end
    status = tag_svc.budget_status(ctx, trip, "CHF")
    assert status.actual == 750 and status.over and status.end is None

    trip = tag_svc.set_budget(ctx, trip.id, 500, "EUR")  # all time, in another currency
    status = tag_svc.budget_status(ctx, trip, "CHF")
    assert status.budget == pytest.approx(round(ctx.convert_on(500, "EUR", "CHF", None), 2))
    assert status.actual == 750
    assert tag_svc.budget_status(ctx, trip, "EUR").budget == 500

    for bad, message in (
        ((-5, "CHF"), "positive"),
        ((5, ""), "Currency"),
        ((5, "CHF", date(2026, 9, 2), date(2026, 9, 1)), "end"),
    ):
        with pytest.raises(ValidationError, match=message):
            tag_svc.set_budget(ctx, trip.id, *bad)
    cleared = tag_svc.set_budget(ctx, trip.id, None)
    assert (cleared.budget, cleared.budget_currency, cleared.budget_start, cleared.has_budget) == (0, "", "", False)
    assert BudgetZero().ratio == 0.0


class BudgetZero(tag_svc.BudgetStatus):
    def __init__(self):
        super().__init__(0.0, 0.0, "CHF", None, None)


# ------------------------------------------------------------------ bulk edit
def test_bulk_edit_adds_and_removes_tags(ctx, setup):
    trip, work = tag_svc.add(ctx, "Trip"), tag_svc.add(ctx, "Work")
    a = tx_svc.save(ctx, spend(setup, tags=[trip.id]))
    b = tx_svc.save(ctx, spend(setup, tags=[work.id]))
    exchange = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 16),
            type="exchange",
            amount=100,
            currency="CHF",
            from_account=setup["chf"].id,
            to_account=setup["eur"].id,
            to_amount=110.5,
        ),
    )
    spread = ctx.store.transactions.get(exchange.linked_id)
    assert not bulk_svc.takes_tags(spread)
    assert not bulk_svc.takes_tags(
        Transaction(date(2026, 1, 1), "income", 1, "CHF", 1, date(2026, 1, 1), invest_id="x")
    )

    ids = [a.id, b.id, exchange.id, spread.id]
    plan = bulk_svc.plan(ctx, ids, BulkChanges(add_tags=["Italy"], remove_tags=[work.id]))
    assert not plan.failures and len(plan.edits) == 3
    assert tag_svc.find(ctx, "Italy") is None  # planning writes nothing
    changed = bulk_svc.bulk_edit(ctx, ids, BulkChanges(add_tags=["Italy"], remove_tags=[work.id]))
    italy = tag_svc.find(ctx, "Italy")
    assert len(changed) == 3
    assert ctx.store.transactions.get(a.id).tags == f"{trip.id}|{italy.id}"
    assert ctx.store.transactions.get(b.id).tags == italy.id
    assert ctx.store.transactions.get(exchange.id).tags == italy.id
    assert ctx.store.transactions.get(spread.id).tags == ""

    # a tag the rows already have, by id or by name, changes nothing
    assert bulk_svc.plan(ctx, [a.id, b.id], BulkChanges(add_tags=["italy"])).edits == []
    assert bulk_svc.plan(ctx, [b.id], BulkChanges(add_tags=[italy.id])).edits == []
    # adding and removing the same tag removes it
    assert bulk_svc.bulk_edit(ctx, [b.id], BulkChanges(add_tags=[italy.id], remove_tags=[italy.id]))
    assert ctx.store.transactions.get(b.id).tags == ""


# ------------------------------------------------------------------ storage and export
def test_tags_are_stored_and_exported(ctx, setup):
    trip = tag_svc.add(ctx, "Trip", "teal")
    tag_svc.set_budget(ctx, trip.id, 800, "EUR", date(2026, 7, 1))
    tx = tx_svc.save(ctx, spend(setup, tags=[trip.id]))
    assert (ctx.store.data_dir / "tags.csv").read_text().splitlines() == [
        "name,colour,budget,budget_currency,budget_start,budget_end,id",
        f"Trip,teal,800.0,EUR,2026-07-01,,{trip.id}",
    ]
    doc = json.loads(export_svc.everything_json(ctx))
    assert doc["tags"] == [
        {
            "name": "Trip",
            "colour": "teal",
            "budget": 800.0,
            "budget_currency": "EUR",
            "budget_start": "2026-07-01",
            "budget_end": "",
            "id": trip.id,
        }
    ]
    assert next(r for r in doc["transactions"] if r["id"] == tx.id)["tags"] == trip.id
    assert Tag("x", budget_start="2026-07-01").start_date == date(2026, 7, 1)
    assert Tag("x").end_date is None
