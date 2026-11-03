"""The To Sort tab: one question per payee for imported transactions without a category, and its count."""

from nicegui import ui
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified
from test_sorting import imported
from test_transactions_page import rows, settle

from budget_app.services import rules as rules_svc
from budget_app.services import sorting
from budget_app.services import transactions as tx_svc


def badges(user: User) -> list[str]:
    return [b.text for b in user.find(kind=ui.badge).elements if b.visible]


def answer_button(user: User, text: str) -> ui.button:
    return next(b for b in user.find(kind=ui.button).elements if b.text == text)


async def test_one_answer_sorts_a_payee_and_the_count_follows(user: User, ctx, setup) -> None:
    for day in (2, 3):
        tx_svc.save(ctx, imported(setup, "Amazon", -20, day))
    tx_svc.save(ctx, imported(setup, "Sandro M.", -30, text="TWINT *Sandro M."))
    await user.open("/to-sort")
    await user.should_see("2 purchases · 40.00 CHF · Sep 2026")
    await user.should_see("What do you usually buy here?")
    await user.should_see("A person you paid by TWINT. What are these payments usually?")
    assert badges(user) == ["2", "2"]  # Transactions in the sidebar, the To Sort tab

    UserInteraction(user, {answer_button(user, "Phones, computers and devices")}, None).click()
    await settle()
    assert notified(user, "2 transactions are now Phones, computers and devices")
    await user.should_see("→ Phones, computers and devices")  # the answered card stays as one line
    assert badges(user) == ["1", "1"]
    assert [r.match for r in rules_svc.all_rules(ctx)] == ["Amazon"]  # remembered, the default

    UserInteraction(user, {answer_button(user, "Gifts and party supplies")}, None).click()
    await settle()
    await user.should_see("Everything is sorted.")
    [title] = [lb for lb in user.find(kind=ui.label).elements if lb.text == "Everything is sorted."]
    assert "group" in title.parent_slot.parent.parent_slot.parent.classes  # a card, like every empty state
    assert sorting.unsorted(ctx) == []


async def test_ask_me_per_purchase_lists_that_payees_transactions(user: User, ctx, setup) -> None:
    for day in (2, 3):
        tx_svc.save(ctx, imported(setup, "Amazon", -20, day))
    tx_svc.save(ctx, imported(setup, "Apple", -5))
    await user.open("/transactions?category=to-sort&payee=Amazon")
    await settle()
    assert [(r["payee"], r["category"]) for r in rows(user)] == [("Amazon", "To sort"), ("Amazon", "To sort")]


async def test_the_overview_shows_spending_to_sort(user: User, ctx, setup) -> None:
    tx_svc.save(ctx, imported(setup, "Amazon", -20))
    await user.open("/")  # the notable spending used to fail on a transaction without a category
    await user.should_see("Overview")


async def test_a_known_brand_shows_its_logo_and_a_person_their_initials(user: User, ctx, setup) -> None:
    tx_svc.save(ctx, imported(setup, "IKEA", -80))
    tx_svc.save(ctx, imported(setup, "Sandro M.", -30, text="TWINT *Sandro M."))
    await user.open("/to-sort")
    await user.should_see("Sandro M.")
    marks = [e for e in user.find(kind=ui.html).elements if "sort-logo" in e.classes]
    assert [m.content.split('aria-label="')[1].split('"')[0] for m in marks] == ["IKEA"]
    assert "SM" in [lb.text for lb in user.find(kind=ui.label).elements if "sort-logo" in lb.classes]
    await user.open("/payees")  # the same marks, small, beside the payees
    await user.should_see("IKEA")
    assert [e for e in user.find(kind=ui.html).elements if "sort-logo" in e.classes and "small" in e.classes]
