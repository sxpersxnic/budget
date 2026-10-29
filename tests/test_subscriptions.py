"""Subscription detection (F17): the rhythm and amount rules, grouping by payee or note, what is left out (booked
rows, running series, ignored groups), the order, the series prefilled from the last charge, the yearly cost of the
running series, and ignoring with undo."""

import json
from datetime import date

import pytest

from budget_app.domain import subscriptions as rules
from budget_app.services import accounts as acc_svc
from budget_app.services import export as export_svc
from budget_app.services import recurring as rec_svc
from budget_app.services import subscriptions as sub_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services.context import ValidationError
from budget_app.services.recurring import RecurringInput
from budget_app.services.transactions import TransactionInput

TODAY = date(2026, 10, 18)
D = date


def spend(ctx, setup, day: date, amount: float, payee: str = "", **kw):
    base = dict(
        date=day,
        type="expenditure",
        amount=amount,
        currency="CHF",
        category="08.3",
        from_account=setup["chf"].id,
        payee=payee,
    )
    return tx_svc.save(ctx, TransactionInput(**(base | kw)))


def monthly(ctx, setup, payee: str, amount: float, day: int = 15, first: int = 3, last: int = 10, **kw):
    return [spend(ctx, setup, D(2026, m, day), amount, payee, **kw) for m in range(first, last + 1)]


# ------------------------------------------------------------------ domain
@pytest.mark.parametrize(
    ("days", "period"),
    [
        (28, 1),
        (30, 1),
        (33, 1),
        (35, 1),
        (36, None),
        (26, 1),
        (25, None),
        (59, 2),
        (62, 2),
        (91, 3),
        (98, 3),
        (99, None),
        (180, 6),
        (190, 6),
        (193, None),
        (365, 12),
        (381, 12),
        (382, None),
        (10, None),
        (500, None),
    ],
)
def test_the_period_of_a_gap(days, period):
    assert rules.period_of_gap(days) == period


def test_the_latest_run_follows_month_ends_and_starts_after_a_break():
    same = [10.0] * 4
    assert rules.latest_run([D(2026, 1, 31), D(2026, 2, 28), D(2026, 3, 31), D(2026, 4, 30)], same) == (1, 0)
    assert rules.latest_run([D(2026, 1, 5), D(2026, 4, 5), D(2026, 7, 5)], same[:3]) == (3, 0)
    skipped = [D(2026, 1, 5), D(2026, 2, 5), D(2026, 4, 5), D(2026, 5, 5)]  # March missing: the run starts in April
    assert rules.latest_run(skipped, same) == (1, 2)
    assert rules.latest_run([D(2026, 1, 5), D(2026, 2, 5), D(2026, 2, 20)], same[:3]) is None  # last gap: no period
    assert rules.latest_run([D(2026, 1, 5)], [10.0]) is None
    assert rules.latest_run([D(2025, 1, 5), D(2026, 1, 5)], same[:2]) == (12, 0)
    price = [D(2026, m, 5) for m in range(1, 7)]
    assert rules.latest_run(price, [12.9, 12.9, 12.9, 18.9, 18.9, 18.9]) == (1, 3)  # the old price ends the run


def test_enough_charges_per_period():
    assert not rules.enough(1, 2) and rules.enough(1, 3)
    assert not rules.enough(3, 2) and rules.enough(3, 3)
    assert rules.enough(6, 2) and rules.enough(12, 2) and not rules.enough(12, 1)


def test_similar_amounts():
    assert rules.similar([49.9, 49.9, 49.9])
    assert rules.similar([100, 119, 81])  # within 20 % of the median
    assert not rules.similar([100, 121, 100])
    assert rules.similar([9.9, 11.9, 9.9])  # 2.00 apart: allowed for small amounts
    assert not rules.similar([9.9, 12.9, 9.9])
    assert not rules.similar([])


def test_next_charge_and_active():
    assert rules.next_charge(D(2026, 1, 31), 1) == D(2026, 2, 28)
    assert rules.next_charge(D(2026, 10, 15), 3) == D(2027, 1, 15)
    assert rules.active(D(2026, 8, 15), 1, TODAY)  # two months ago: still running
    assert not rules.active(D(2026, 7, 15), 1, TODAY)  # three months without a charge: stopped
    assert rules.active(D(2025, 1, 5), 12, TODAY)
    assert not rules.active(D(2024, 1, 5), 12, TODAY)


def test_detect_sums_a_day_and_judges_the_run():
    charges = [(D(2026, m, 15), 49.9) for m in range(3, 11)]
    found = rules.detect(charges, TODAY)
    assert found == rules.Rhythm(1, 49.9, D(2026, 3, 15), D(2026, 10, 15), 8)
    assert found.yearly == pytest.approx(598.8)
    split = [(D(2026, 9, 15), 20.0), (D(2026, 9, 15), 29.9)]  # one bill paid in two parts
    assert rules.detect(charges[:6] + split, TODAY).count == 7
    assert rules.detect([], TODAY) is None
    assert rules.detect(charges[:2], TODAY) is None  # too few
    assert rules.detect(charges[:3], TODAY) is None  # stopped in May
    assert rules.detect([(D(2026, m, 15), 30.0 + 60 * (m % 2)) for m in range(3, 11)], TODAY) is None  # erratic
    two = [(D(2026, 8, 15), 30.0), (D(2026, 9, 15), 90.0)]
    assert rules.detect(charges[:6] + two, TODAY) is None  # the last two differ: no run at all
    late = [*charges[:3], (D(2026, 6, 25), 49.9), *charges[4:]]  # June paid late: the run starts in July
    assert rules.detect(late, TODAY) == rules.Rhythm(1, 49.9, D(2026, 7, 15), D(2026, 10, 15), 4)
    yearly = [(D(2025, 1, 5), 1200.0), (D(2026, 1, 5), 1250.0)]
    assert rules.detect(yearly, TODAY).period == 12
    assert rules.detect(yearly, TODAY).amount == 1225.0


# ------------------------------------------------------------------ the service
def test_groups_by_payee_key_or_note_and_leaves_booked_rows_out(ctx, setup):
    spend(ctx, setup, D(2026, 3, 15), 49.9, "Swisscom")
    spend(ctx, setup, D(2026, 4, 15), 49.9, "SWISSCOM")
    spend(ctx, setup, D(2026, 5, 15), 49.9, "swisscom ")
    spend(ctx, setup, D(2026, 6, 15), 49.9, comment="Mobile")
    spend(ctx, setup, D(2026, 7, 15), 49.9, comment="mobile")
    spend(ctx, setup, D(2026, 8, 15), 49.9)  # neither: not grouped
    booked = spend(ctx, setup, D(2026, 9, 15), 49.9, "Swisscom")
    booked.recurring_id = "series"
    ctx.store.transactions.update(booked)
    found = sub_svc.groups(ctx)
    assert {k: len(v) for k, v in found.items()} == {("swisscom", "CHF"): 3, ("note:mobile", "CHF"): 2}
    assert [tx.date.month for tx in found[("swisscom", "CHF")]] == [3, 4, 5]


def test_detects_subscriptions_with_their_facts_most_expensive_first(ctx, setup):
    monthly(ctx, setup, "Swisscom", 49.9)
    monthly(ctx, setup, "ZVV", 85, day=2, first=1, category="07.3")
    monthly(ctx, setup, "Migros", 60, day=3)
    for m, amount in ((8, 30), (9, 90), (10, 45)):
        spend(ctx, setup, D(2026, m, 20), amount, "Migros", category="01.1")  # irregular on top: not a rhythm
    found = sub_svc.detect(ctx, "CHF", TODAY)
    assert [(s.name, s.period, s.amount, s.count) for s in found] == [("ZVV", 1, 85, 10), ("Swisscom", 1, 49.9, 8)]
    zvv, swisscom = found
    assert zvv.yearly == pytest.approx(1020) and zvv.yearly_view == pytest.approx(1020)
    assert zvv.category == "07.3" and zvv.account == setup["chf"].id and zvv.currency == "CHF"
    assert zvv.first == D(2026, 1, 2) and zvv.last == D(2026, 10, 2) and zvv.next == D(2026, 11, 2)
    assert len(zvv.transaction_ids) == 10 and zvv.last_id == ctx.store.transactions.get(zvv.last_id).id
    assert swisscom.key == "swisscom" and swisscom.yearly == pytest.approx(598.8)


def test_older_history_is_left_out_of_the_facts(ctx, setup):
    card = acc_svc.add_account(ctx, "Card", "CHF")
    for m in (1, 3):
        spend(ctx, setup, D(2026, m, 15), 12.9, "Netflix", category="09.4", from_account=card.id)
    monthly(ctx, setup, "Netflix", 18.9, first=6, last=10, category="09.4.2")  # resumed at a new price
    [found] = sub_svc.detect(ctx, "CHF", TODAY)
    assert (found.first, found.count, found.amount) == (D(2026, 6, 15), 5, 18.9)
    assert len(found.transaction_ids) == 5 and found.category == "09.4.2" and found.account == setup["chf"].id


def test_the_last_spelling_names_it_and_the_most_frequent_category_and_account_count(ctx, setup):
    monthly(ctx, setup, "Netflix", 15.9, first=3, last=6, category="09.4")
    monthly(ctx, setup, "NETFLIX", 15.9, first=7, last=10, category="09.4.2")
    spend(ctx, setup, D(2026, 10, 15), 0.01, "Netflix", category="09.4")  # the same day: summed, still similar
    [found] = sub_svc.detect(ctx, "CHF", TODAY)
    assert found.name == "Netflix" and found.category == "09.4"


def test_a_running_series_and_ignored_groups_are_left_out(ctx, setup):
    monthly(ctx, setup, "Swisscom", 49.9)
    monthly(ctx, setup, "ZVV", 85, day=2)
    monthly(ctx, setup, "Gym", 79, day=1, comment="Fitness")
    rec_svc.save(
        ctx,
        RecurringInput(
            amount=49.9,
            currency="CHF",
            category="08.3",
            from_account=setup["chf"].id,
            start=D(2026, 11, 15),
            payee="SWISSCOM",
        ),
    )
    ended = rec_svc.save(
        ctx,
        RecurringInput(
            amount=85,
            currency="CHF",
            category="07.3",
            from_account=setup["chf"].id,
            start=D(2026, 1, 2),
            end=D(2026, 1, 2),
            payee="ZVV",
        ),
    )
    rec_svc.skip(ctx, ended.id, D(2026, 1, 2))  # its only occurrence handled: the series has ended
    assert rec_svc.ended(ctx.store.recurring.get(ended.id))
    assert sub_svc.already_recurring(ctx) == {("swisscom", "CHF")}
    names = [s.name for s in sub_svc.detect(ctx, "CHF", TODAY)]
    assert names == ["ZVV", "Gym"]  # the ended series doesn't cover ZVV
    gym = sub_svc.detect(ctx, "CHF", TODAY)[1]
    item = sub_svc.ignore(ctx, gym)
    assert (item.key, item.currency, item.name) == ("gym", "CHF", "Gym")
    assert [s.name for s in sub_svc.detect(ctx, "CHF", TODAY)] == ["ZVV"]
    assert sub_svc.ignore(ctx, gym) == item  # once
    assert len(ctx.store.subscription_ignores.all()) == 1
    assert undo_svc.latest(ctx).label == "Ignore a subscription"
    assert sub_svc.unignore(ctx, item.id) == 1
    assert [s.name for s in sub_svc.detect(ctx, "CHF", TODAY)] == ["ZVV", "Gym"]
    assert undo_svc.latest(ctx).label == "Watch a subscription again"
    with pytest.raises(ValidationError, match="not ignored"):
        sub_svc.unignore(ctx, item.id)
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert [i.name for i in sub_svc.ignored(ctx)] == ["Gym"]


def test_another_currency_is_valued_at_the_latest_rate(ctx, setup):
    monthly(ctx, setup, "Spotify", 10, currency="EUR", from_account=setup["eur"].id)
    [found] = sub_svc.detect(ctx, "CHF", TODAY)
    assert found.currency == "EUR" and found.yearly == pytest.approx(120)
    assert found.yearly_view == pytest.approx(120 * 0.9)  # 0.90 CHF per EUR on the latest date
    assert sub_svc.detect(ctx, "EUR", TODAY)[0].yearly_view == pytest.approx(120)


def test_the_series_prefilled_from_the_last_charge(ctx, setup):
    monthly(ctx, setup, "Swisscom", 49.9, last=9, comment="Mobile", tags=["Phone"])
    last = spend(ctx, setup, D(2026, 10, 15), 52.9, "Swisscom", comment="Mobile, new plan")  # a small price rise
    [found] = sub_svc.detect(ctx, "CHF", TODAY)
    inp = sub_svc.to_recurring(ctx, found)
    assert inp.start == D(2026, 10, 15) and inp.recur_months == 1 and inp.amount == 52.9
    assert inp.payee == "Swisscom" and inp.category == "08.3" and inp.from_account == setup["chf"].id
    assert inp.comment == "Mobile, new plan"
    series = rec_svc.save(ctx, inp, first_booked=found.last_id)
    assert series.next_due == D(2026, 11, 15)
    assert ctx.store.transactions.get(last.id).recurring_id == series.id
    assert sub_svc.detect(ctx, "CHF", TODAY) == []  # the series now covers it
    ctx.store.transactions.delete([last.id])
    with pytest.raises(ValidationError, match="not found"):
        sub_svc.to_recurring(ctx, found)


def test_recurring_spend_lists_the_running_expenditure_series(ctx, setup):
    rent = RecurringInput(
        amount=1900, currency="CHF", category="04.1", from_account=setup["chf"].id, start=D(2026, 1, 1)
    )
    rec_svc.save(ctx, rent)
    rec_svc.save(
        ctx,
        RecurringInput(
            **(
                rent.__dict__
                | {
                    "amount": 10,
                    "category": "08.3",
                    "currency": "EUR",
                    "from_account": setup["eur"].id,
                    "recur_months": 3,
                    "payee": "Spotify",
                }
            )
        ),
    )
    over = rec_svc.save(ctx, RecurringInput(**(rent.__dict__ | {"end": D(2026, 1, 1)})))
    rec_svc.skip(ctx, over.id, D(2026, 1, 1))  # its only occurrence handled: ended, so not a cost
    rec_svc.save(
        ctx,
        RecurringInput(
            type="income",
            amount=5000,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
            start=D(2026, 1, 25),
        ),
    )
    rows = sub_svc.recurring_spend(ctx, "CHF")
    assert [(r.title, r.yearly, round(r.yearly_view, 2)) for r in rows] == [
        ("04.1 Rent", 22800, 22800),
        ("Spotify · 08.3 Phone and internet", 40, 36),
    ]


def test_the_ignores_are_a_table_and_in_the_json_export(ctx, setup):
    assert "subscription_ignores" in ctx.store.tables()
    monthly(ctx, setup, "Gym", 79)
    sub_svc.ignore(ctx, sub_svc.detect(ctx, "CHF", TODAY)[0])
    doc = json.loads(export_svc.everything_json(ctx))
    assert doc["subscription_ignores"][0]["key"] == "gym" and doc["subscription_ignores"][0]["name"] == "Gym"
    assert (ctx.store.data_dir / "subscription_ignores.csv").exists()
