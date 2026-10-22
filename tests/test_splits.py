"""Split transactions (F11, decision 14): a category (and tags) per item line, counted in every sum by category or
tag, never in the net totals and the reconciliation."""

import random
from datetime import date

import pytest

from budget_app.domain import splits
from budget_app.domain.coicop import ADV, TAX
from budget_app.domain.models import TX_EXPENDITURE, TX_INCOME
from budget_app.domain.splits import Line, Part
from budget_app.services import aggregates, reports
from budget_app.services import budget as budget_svc
from budget_app.services import bulk as bulk_svc
from budget_app.services import export as export_svc
from budget_app.services import items as items_svc
from budget_app.services import tags as tag_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import undo as undo_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.bulk import BulkChanges
from budget_app.services.context import ValidationError
from budget_app.services.items import ItemInput
from budget_app.services.transactions import TransactionInput
from budget_app.storage.store import Store
from budget_app.ui.pages.categories import spent_this_year

TODAY = date(2026, 10, 18)
SEPT = {"2026-09"}


def spend(setup, amount=100.0, currency="CHF", **kw) -> TransactionInput:
    account = setup["eur"].id if currency == "EUR" else setup["chf"].id
    base = dict(
        date=date(2026, 9, 10),
        type=TX_EXPENDITURE,
        amount=amount,
        currency=currency,
        category="01.1",
        from_account=account,
    )
    return TransactionInput(**(base | kw))


def receipt(ctx, setup, lines, **kw):
    """An expenditure with item lines given as (name, total, category[, tags])."""
    tx = tx_svc.save(ctx, spend(setup, **kw))
    items_svc.replace(ctx, tx.id, [ItemInput(n, 1, p, c, list(rest[0]) if rest else []) for n, p, c, *rest in lines])
    return ctx.store.transactions.get(tx.id)


def cents(parts: list[Part]) -> tuple[int, int]:
    return round(sum(p.amount for p in parts) * 100), round(sum(p.amount_chf for p in parts) * 1_000_000)


# ------------------------------------------------------------------ the rule (domain)
def test_lines_with_a_category_count_there_and_the_rest_stays():
    parts = splits.parts(100, 100, "01.1", [Line(40, "09.1"), Line(10), Line(5, "01.1")])
    assert parts == [Part("01.1", 60.0, 60.0), Part("09.1", 40.0, 40.0)]
    assert splits.rest(100, [Line(40, "09.1"), Line(10)], "01.1") == 60
    assert splits.split_total([Line(40, "09.1"), Line(10)], "01.1") == 40
    # nothing to split: the transaction as it is
    assert splits.parts(12.34, 11.123456, "01.1", [Line(12.34)], tags="a") == [Part("01.1", 12.34, 11.123456, "a")]


def test_chf_follows_the_transactions_rate_and_adds_up_to_the_cent():
    # 100 EUR booked as 93.123456 CHF: each line at the same rate, rounded; the rest takes what is left
    parts = splits.parts(100, 93.123456, "01.1", [Line(33.33, "09.1"), Line(33.33, "05.6")])
    assert [(p.category, p.amount, p.amount_chf) for p in parts] == [
        ("01.1", 33.34, pytest.approx(31.043456)),
        ("09.1", 33.33, 31.04),
        ("05.6", 33.33, 31.04),
    ]
    assert cents(parts) == (10000, 93123456)


def test_a_fully_split_amount_leaves_no_stray_cent_in_its_own_category():
    parts = splits.parts(100, 93.123456, "01.1", [Line(40, "09.1"), Line(60, "05.6")])
    assert [p.category for p in parts] == ["09.1", "05.6"]  # the CHF rounding went to the largest line
    assert parts[1].amount_chf == pytest.approx(55.873456)
    assert cents(parts) == (10000, 93123456)


def test_a_discount_line_lowers_its_category_and_raises_the_rest():
    parts = splits.parts(20, 20, "01.1", [Line(8, "05.6"), Line(-3, "05.6"), Line(-1, "09.1")])
    assert parts == [Part("01.1", 16.0, 16.0), Part("05.6", 5.0, 5.0), Part("09.1", -1.0, -1.0)]
    assert splits.overflow(20, [Line(25, "05.6"), Line(-6, "05.6")], "01.1") == 0  # 19 fits
    assert splits.overflow(20, [Line(25, "05.6"), Line(-4, "05.6")], "01.1") == 1


def test_line_tags_count_with_the_transactions_tags():
    parts = splits.parts(50, 50, "01.1", [Line(10, "", "gift"), Line(5, "09.1", "gift|kids")], tags="trip")
    assert parts == [
        Part("01.1", 35.0, 35.0, "trip"),
        Part("01.1", 10.0, 10.0, "gift|trip"),
        Part("09.1", 5.0, 5.0, "gift|kids|trip"),
    ]
    assert parts[2].tag_ids == ["gift", "kids", "trip"]


def test_an_advance_is_shared_proportionally_by_every_part():
    parts = splits.parts(100, 100, "01.1", [Line(40, "09.1")], advance=50)
    assert parts == [Part("01.1", 30.0, 30.0), Part("09.1", 20.0, 20.0), Part(ADV, 50.0, 50.0)]
    # rounding: 50 % of 68.67 and of 33.33 round up; the rest gives the cent back
    parts = splits.parts(100, 93.123456, "01.1", [Line(33.33, "09.1"), Line(-2, "05.6")], advance=50)
    assert [(p.category, p.amount) for p in parts] == [("01.1", 34.34), ("09.1", 16.66), ("05.6", -1.0), (ADV, 50.0)]
    assert parts[-1].amount_chf == pytest.approx(46.56)  # the advance at the transaction's rate, to the cent
    assert cents(parts) == (10000, 93123456)
    # all of it paid for others: nothing stays in a category
    assert splits.parts(80, 75.5, "11.1", [Line(30, "09.1")], advance=80) == [Part(ADV, 80.0, 75.5)]
    # an unsplit advance as before
    assert splits.parts(80, 80, "11.1", advance=30) == [Part("11.1", 50.0, 50.0), Part(ADV, 30.0, 30.0)]


def test_parts_always_add_up_exactly():
    rnd = random.Random(11)  # noqa: S311 - test data, the same on every run
    codes = ["01.1", "05.6", "09.1", "12.1", TAX]
    for _ in range(400):
        amount = rnd.randint(1, 200_000) / 100
        rate = rnd.uniform(0.5, 1.6)
        amount_chf = round(amount * rate, 6)
        lines = []
        budget = round(amount * 100)
        for _ in range(rnd.randint(0, 6)):
            total = rnd.randint(-500, max(1, budget // 3))
            budget -= max(total, 0)
            lines.append(Line(total / 100, rnd.choice(["", *codes]), rnd.choice(["", "t1", "t1|t2"])))
        if splits.overflow(amount, lines, "01.1"):
            continue
        advance = rnd.choice([0, 0, rnd.randint(1, round(amount * 100)) / 100, amount])
        parts = splits.parts(amount, amount_chf, "01.1", lines, advance)
        assert cents(parts) == (round(amount * 100), round(amount_chf * 1_000_000))
        assert all(abs(round(p.amount * 100) - p.amount * 100) < 1e-6 for p in parts)  # whole cents
        adv = [p for p in parts if p.category == ADV]
        assert round(sum(p.amount for p in adv) * 100) == round(advance * 100)
        own = [p for p in parts if p.category != ADV]
        for code in {line.category for line in lines if line.category and line.category != "01.1"}:
            expected = sum(round(line.total * 100) for line in lines if line.category == code)
            got = sum(round(p.amount * 100) for p in own if p.category == code)
            assert abs(got - expected * (1 - advance / amount)) <= len(lines) + 1


# ------------------------------------------------------------------ storing split lines
def test_lines_keep_their_category_and_tags(ctx, setup):
    tx = receipt(ctx, setup, [("Milk", 4.5, ""), ("Soap", 6.2, "05.6"), ("Card", 3.0, "09.1", ["Birthday"])])
    stored = items_svc.items_for(ctx, tx.id)
    birthday = tag_svc.find(ctx, "Birthday")
    assert birthday is not None  # a new tag name of a line becomes a tag when the line is stored
    assert [(i.name, i.category, i.tags) for i in stored] == [
        ("Milk", "", ""),
        ("Soap", "05.6", ""),
        ("Card", "09.1", birthday.id),
    ]
    # a line with the transaction's own category simply belongs to the rest
    items_svc.replace(ctx, tx.id, [ItemInput("Bread", 1, 3.0, "01.1")])
    assert items_svc.items_for(ctx, tx.id)[0].category == ""


def test_old_item_files_load_without_categories(ctx, setup, tmp_path):
    tx = receipt(ctx, setup, [("Milk", 4.5, "")])
    path = tmp_path / "transaction_items.csv"
    path.write_text(f"transaction_id,name,quantity,unit_price,position,id\n{tx.id},Milk,1.0,4.5,0,abc123\n")
    store = Store(tmp_path)
    [item] = store.transaction_items.all()
    assert (item.name, item.category, item.tags) == ("Milk", "", "")


def test_split_lines_above_the_amount_are_refused(ctx, setup):
    tx = tx_svc.save(ctx, spend(setup, 10))
    with pytest.raises(ValidationError, match=r"add up to 12\.00 CHF, 2\.00 CHF more than the amount"):
        items_svc.replace(ctx, tx.id, [ItemInput("TV", 1, 12, "09.1"), ItemInput("Bag", 1, 5)])
    assert items_svc.items_for(ctx, tx.id) == []
    items_svc.check_split([ItemInput("TV", 1, 12)], 10, "01.1")  # lines without a category may exceed it
    items_svc.check_split([ItemInput("TV", 1, 12, "09.1")], None, "01.1")  # no amount yet: nothing to check
    with pytest.raises(ValidationError, match="Item 1: select an expenditure category"):
        items_svc.replace(ctx, tx.id, [ItemInput("TV", 1, 5, "99.9")])
    items_svc.replace(ctx, tx.id, [ItemInput("Tax", 1, 5, TAX)])  # taxes are a category too


def test_only_expenditure_keeps_line_categories(ctx, setup):
    income = tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 25),
            type=TX_INCOME,
            amount=100,
            currency="CHF",
            category=setup["salary"].id,
            to_account=setup["chf"].id,
        ),
    )
    items_svc.replace(ctx, income.id, [ItemInput("Bonus", 1, 150, "09.1", ["Work"])])  # not checked, not kept
    [item] = items_svc.items_for(ctx, income.id)
    assert (item.category, item.tags) == ("", "")
    assert tag_svc.find(ctx, "Work") is None
    assert aggregates.transaction_parts(ctx, income) == [Part(setup["salary"].id, 100, 100)]


def test_saving_items_is_undone_with_their_transaction(ctx, setup):
    with ctx.store.history.action("Save a transaction"):
        tx = tx_svc.save(ctx, spend(setup, 30))
        items_svc.replace(ctx, tx.id, [ItemInput("Card", 1, 3, "09.1", ["Gift"])])
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert ctx.store.transactions.all() == [] and ctx.store.transaction_items.all() == []
    assert ctx.store.tags.all() == []


# ------------------------------------------------------------------ every sum by category
def test_reports_budgets_and_charts_count_each_line_in_its_category(ctx, setup):
    receipt(ctx, setup, [("Food", 60, ""), ("Soap", 25.5, "05.6"), ("Gift", 14.5, "09.1")])
    tx_svc.save(ctx, spend(setup, 40, category="05.6"))
    for code, amount in (("01", 100), ("05", 30), ("09", 10)):
        budget_svc.create(
            ctx,
            BudgetInput(start_month="2026-09", end_month="2026-09", type="expenditure", currency="CHF", amount=amount,
                        category=code),
        )  # fmt: skip

    assert aggregates.actual_expenditure(ctx, SEPT, "CHF") == pytest.approx({"01.1": 60, "05.6": 65.5, "09.1": 14.5})
    rep = reports.month_report(ctx, "2026-09", "CHF", TODAY)
    tree = rep.expenditure_tree
    assert (tree["01"].actual, tree["05"].actual, tree["09"].actual) == pytest.approx((60, 65.5, 14.5))
    assert (tree["05"].budget, tree["05"].diff) == pytest.approx((30, 35.5))
    assert rep.total_expenditure.actual == pytest.approx(140)  # the total stays the transactions'
    # the projection's "higher of recorded and budget" per division (F47): 100 + 65.5 + 14.5
    assert aggregates.projected_net(ctx, "2026-09", "CHF") == pytest.approx(-180)
    row = reports.monthly_series(ctx, "2026-09", "2026-09", "CHF", ["EXP:05", "EXP:09", "EXP:TOTAL"], True)[0]
    labels = reports.series_keys(ctx)
    assert row[labels["EXP:05"]] == 65.5 and row[labels["EXP:09"]] == 14.5 and row[labels["EXP:TOTAL"]] == 140
    assert row[reports.budget_label(labels["EXP:05"])] == 30
    # the statement export and the COICOP page
    frames = export_svc.statement_frames(rep)
    sheet = next(df for name, df in frames.items() if "xpenditure" in name)
    assert "09.1" in " ".join(sheet.iloc[:, 0].astype(str))
    assert spent_this_year(ctx, TODAY)["09"] == pytest.approx(14.5)


def test_a_foreign_split_counts_at_the_transactions_rate(ctx, setup):
    tx = receipt(ctx, setup, [("Book", 33.33, "09.1")], amount=50, currency="EUR")
    assert tx.amount_chf == pytest.approx(47.5)  # 0.95 CHF per EUR
    chf = aggregates.actual_expenditure(ctx, SEPT, "CHF")
    assert chf == {"01.1": pytest.approx(47.5 - 31.66), "09.1": pytest.approx(31.66)}  # 33.33 × 0.95, to the cent
    assert aggregates.actual_expenditure(ctx, SEPT, "EUR") == {"01.1": pytest.approx(16.67), "09.1": 33.33}
    usd = aggregates.actual_expenditure(ctx, SEPT, "USD")
    assert sum(usd.values()) == pytest.approx(aggregates.tx_value(ctx, tx, "USD"))


def test_a_split_never_changes_net_balances_or_the_unexplained_difference(ctx, setup):
    chf, eur, salary = setup["chf"].id, setup["eur"].id, setup["salary"].id
    for acc, amount in ((chf, 1000), (eur, 200)):
        wealth_svc.upsert_entry(ctx, acc, "2026-08", amount)
    tx_svc.save(
        ctx,
        TransactionInput(date=date(2026, 9, 25), type=TX_INCOME, amount=800, currency="CHF", category=salary,
                         to_account=chf),
    )  # fmt: skip
    a = tx_svc.save(ctx, spend(setup, 123.45))
    b = tx_svc.save(ctx, spend(setup, 77.77, currency="EUR"))
    c = tx_svc.save(ctx, spend(setup, 50, is_advance=True, advance_mode="percent", advance_value=40, debtor="Mia"))
    wealth_svc.upsert_entry(ctx, chf, "2026-09", 1000 + 800 - 123.45 - 50 - 300)  # 300 left unrecorded
    wealth_svc.upsert_entry(ctx, eur, "2026-09", 200 - 77.77)

    def snapshot():
        out = []
        for view in ("CHF", "EUR", "USD"):
            rep = reports.month_report(ctx, "2026-09", view, TODAY)
            out.append(
                (
                    rep.total_income.actual,
                    rep.total_expenditure.actual,
                    rep.wealth_start,
                    rep.wealth_end,
                    rep.balance_change,
                    rep.unexplained,
                    [(r.account.id, r.opening, r.amount, r.flows, r.unexplained) for r in rep.accounts],
                    sum(rep.expenditure_tree[code].actual for code in rep.top_expenditure),
                )
            )
            out.append(aggregates.projected_net(ctx, "2026-10", view))
            out.append(wealth_svc.projected_wealth(ctx, "2026-12", view))
        return out

    before = snapshot()
    items_svc.replace(ctx, a.id, [ItemInput("Soap", 1, 23.45, "05.6"), ItemInput("Gift", 3, 33.33, "09.1")])
    items_svc.replace(ctx, b.id, [ItemInput("Book", 1, 40.01, "09.1"), ItemInput("Coupon", 1, -5, "05.6")])
    items_svc.replace(ctx, c.id, [ItemInput("Cake", 1, 19.99, "11.1")])
    after = snapshot()
    for old, new in zip(before, after, strict=True):
        assert new == pytest.approx(old, abs=1e-9)
    assert reports.month_report(ctx, "2026-09", "CHF", TODAY).unexplained == pytest.approx(-300, abs=0.01)
    # and the categories did change
    assert aggregates.actual_expenditure(ctx, SEPT, "CHF")["09.1"] == pytest.approx(
        99.99 + 38.01
    )  # 40.01 EUR × 0.95, to the cent


# ------------------------------------------------------------------ advances
def test_a_split_advance_is_repaid_and_settled_as_before(ctx, setup):
    tx = receipt(
        ctx,
        setup,
        [("Dinner", 60, "11.1"), ("Wine", 20, "02.1")],
        amount=100,
        is_advance=True,
        advance_mode="amount",
        advance_value=50,
        debtor="Mia",
    )
    assert aggregates.expenditure_parts(ctx, tx, "CHF") == [("01.1", 10.0), ("11.1", 30.0), ("02.1", 10.0), (ADV, 50.0)]
    tree = reports.month_report(ctx, "2026-09", "CHF", TODAY).expenditure_tree
    assert (tree["11"].actual, tree[ADV].actual) == (30, 50)
    [open_one] = tx_svc.open_advances(ctx, tx.advance_account)
    assert open_one.outstanding == 50
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 20),
            type=TX_INCOME,
            amount=50,
            currency="CHF",
            to_account=setup["chf"].id,
            is_advance=True,
            advance_account=tx.advance_account,
            settles_id=tx.id,
        ),
    )
    assert tx_svc.open_advances(ctx, tx.advance_account) == []
    # the repayment comes back as income, not into the split categories
    rep = reports.month_report(ctx, "2026-09", "CHF", TODAY)
    assert rep.total_income.actual == 50 and rep.expenditure_tree["11"].actual == 30
    # the lines cannot lower the advance below what was repaid, and the edit lock still holds
    edit = tx_svc.edit_input(ctx, tx)
    edit.advance_value = 40
    with pytest.raises(ValidationError, match="already been repaid"):
        tx_svc.save(ctx, edit, tx.id)


# ------------------------------------------------------------------ tags per line
def test_a_lines_tags_count_only_that_line(ctx, setup):
    trip, gift = tag_svc.add(ctx, "Trip"), tag_svc.add(ctx, "Gift")
    tx = receipt(ctx, setup, [("Food", 60, ""), ("Card", 15, "09.1", [gift.id])], tags=[trip.id])
    totals = tag_svc.totals(ctx, "CHF")
    assert (totals[trip.id].spent, totals[trip.id].count) == (100, 1)  # the transaction's tag: all of it
    assert (totals[gift.id].spent, totals[gift.id].count) == (15, 1)  # the line's tag: that line
    assert tag_svc.transaction_tags(tx, aggregates.split_lines(ctx)[tx.id]) == [trip.id, gift.id]
    # an advance: the tags count the own share only
    other = receipt(
        ctx, setup, [("Card", 20, "", [gift.id])], amount=40, is_advance=True, advance_value=50, debtor="Mia"
    )
    assert tag_svc.totals(ctx, "CHF")[gift.id].spent == pytest.approx(15 + 10)
    gift_budget = tag_svc.set_budget(ctx, gift.id, 100, "CHF")
    assert tag_svc.budget_status(ctx, gift_budget, "CHF").actual == 25
    assert other.id in {a.transaction_id for a in tag_svc.tagged_amounts(ctx, "CHF") if a.tag_id == gift.id}


def test_deleting_and_merging_tags_rewrites_the_lines(ctx, setup):
    gift, present = tag_svc.add(ctx, "Gift"), tag_svc.add(ctx, "Present")
    tx = receipt(ctx, setup, [("Card", 15, "09.1", [present.id])])
    tag_svc.merge(ctx, [gift.id, present.id], gift.id)
    assert items_svc.items_for(ctx, tx.id)[0].tags == gift.id
    assert tag_svc.delete(ctx, [gift.id]) == 1  # one transaction loses it, on a line
    assert items_svc.items_for(ctx, tx.id)[0].tags == ""
    undo_svc.undo(ctx, undo_svc.latest(ctx).id)
    assert items_svc.items_for(ctx, tx.id)[0].tags == gift.id


# ------------------------------------------------------------------ bulk edit
def test_bulk_edit_changes_only_the_rest_of_a_split(ctx, setup):
    tx = receipt(ctx, setup, [("Food", 60, ""), ("Card", 15, "09.1")])
    plain = tx_svc.save(ctx, spend(setup, 20))
    bulk_svc.bulk_edit(ctx, [tx.id, plain.id], BulkChanges(expenditure_category="01.2"))
    assert aggregates.expenditure_parts(ctx, ctx.store.transactions.get(tx.id), "CHF") == [("01.2", 85), ("09.1", 15)]
    assert items_svc.items_for(ctx, tx.id)[1].category == "09.1"
    assert aggregates.is_split(ctx, ctx.store.transactions.get(tx.id))
    assert not aggregates.is_split(ctx, ctx.store.transactions.get(plain.id))
    # a line that now has the transaction's category no longer splits it
    bulk_svc.bulk_edit(ctx, [tx.id], BulkChanges(expenditure_category="09.1"))
    assert aggregates.expenditure_parts(ctx, ctx.store.transactions.get(tx.id), "CHF") == [("09.1", 100)]


def test_the_json_export_has_the_line_categories(ctx, setup):
    import json  # noqa: PLC0415

    receipt(ctx, setup, [("Card", 15, "09.1")])
    data = json.loads(export_svc.everything_json(ctx))
    assert data["transaction_items"][0]["category"] == "09.1"
