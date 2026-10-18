from datetime import date

import pytest

from budget_app.domain.models import TX_EXPENDITURE
from budget_app.services import items as items_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.context import ValidationError
from budget_app.services.items import ItemInput
from budget_app.services.transactions import TransactionInput
from budget_app.storage.store import Store


@pytest.fixture
def receipt(ctx, setup):
    return tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 10, 3),
            type=TX_EXPENDITURE,
            amount=12.40,
            currency="CHF",
            category="01.1",
            from_account=setup["chf"].id,
        ),
    )


def test_items_are_saved_in_order_and_totalled(ctx, receipt):
    saved = items_svc.replace(
        ctx,
        receipt.id,
        [
            ItemInput("Milk", 2, 1.65),
            ItemInput("", 1, None),  # blank line: ignored
            ItemInput("  Bread ", 1, 3.20),
            ItemInput("Coupon", 1, -0.50),  # discounts are negative prices
        ],
    )
    assert [i.name for i in saved] == ["Milk", "Bread", "Coupon"]
    assert [i.total for i in saved] == [3.30, 3.20, -0.50]
    assert items_svc.total(saved) == 6.00

    # reload from disk: order, values and the link to the transaction survive
    fresh = Store(ctx.store.data_dir)
    rows = sorted((i for i in fresh.transaction_items.all()), key=lambda i: i.position)
    assert [(i.name, i.quantity, i.unit_price) for i in rows] == [
        ("Milk", 2, 1.65),
        ("Bread", 1, 3.2),
        ("Coupon", 1, -0.5),
    ]
    assert {i.transaction_id for i in rows} == {receipt.id}


def test_replacing_and_clearing_a_list(ctx, receipt):
    items_svc.replace(ctx, receipt.id, [ItemInput("Milk", 1, 1.65), ItemInput("Eggs", 1, 4.95)])
    items_svc.replace(ctx, receipt.id, [ItemInput("Eggs", 2, 4.95)])
    assert [(i.name, i.quantity) for i in items_svc.items_for(ctx, receipt.id)] == [("Eggs", 2)]
    assert items_svc.replace(ctx, receipt.id, []) == []
    assert items_svc.items_for(ctx, receipt.id) == []


@pytest.mark.parametrize(
    ("item", "message"),
    [
        (ItemInput("", 1, 2.0), "Item 1: enter a name"),
        (ItemInput("Milk", 1, None), "Item 1: enter a price"),
        (ItemInput("Milk", 0, 2.0), "Item 1: the quantity must be greater than 0"),
    ],
)
def test_half_filled_items_are_rejected(ctx, receipt, item, message):
    items_svc.replace(ctx, receipt.id, [ItemInput("Bread", 1, 3.2)])
    with pytest.raises(ValidationError, match=message):
        items_svc.replace(ctx, receipt.id, [item])
    assert [i.name for i in items_svc.items_for(ctx, receipt.id)] == ["Bread"]  # unchanged


def test_deleting_a_transaction_deletes_its_items(ctx, receipt):
    items_svc.replace(ctx, receipt.id, [ItemInput("Milk", 1, 1.65)])
    tx_svc.delete(ctx, [receipt.id])
    assert ctx.store.transaction_items.all() == []


def test_counts_names_and_suggestions(ctx, receipt):
    items_svc.replace(ctx, receipt.id, [ItemInput("Milk", 1, 1.65), ItemInput("Bread", 1, 3.2)])
    assert items_svc.counts(ctx)[receipt.id] == 2
    assert items_svc.names_by_transaction(ctx)[receipt.id] == "Milk Bread"
    assert set(items_svc.known_names(ctx)) == {"Milk", "Bread"}


def test_data_folders_without_items_still_load(tmp_path):
    store = Store(tmp_path)  # creates an empty transaction_items.csv next to the other tables
    assert store.transaction_items.all() == []
    assert (tmp_path / "transaction_items.csv").exists()
