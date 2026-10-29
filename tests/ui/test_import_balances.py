"""Month-end balances in the import's review step (F36): from a CSV balance column or a camt closing balance."""

from nicegui import ElementFilter, ui
from nicegui.testing import User
from notifications import notified
from test_camt import entry, statement
from test_closing_balances import OLDEST_FIRST, closing
from test_import_camt import upload_xml
from test_import_page import categorise, click, field, import_button, put, settle, status, upload

from budget_app.services import importer, wealth


def checkboxes(user: User) -> list[ui.checkbox]:
    """The balance checkboxes in page order (none when no balance is offered)."""
    with user:
        return sorted(ElementFilter(kind=ui.checkbox, local_scope=False), key=lambda c: c.id)


def ticked(user: User) -> list[tuple[str, bool]]:
    return [(c.text, c.value) for c in checkboxes(user)]


async def oldest_first_on_chf(user: User, setup) -> None:
    await user.open("/import")
    await upload(user, "bank.csv", OLDEST_FIRST.encode())
    put(user, field(user, ui.select, "Account"), setup["chf"].id)


async def test_the_balances_of_each_month_are_offered_and_recorded(user: User, ctx, setup) -> None:
    chf = setup["chf"].id
    wealth.upsert_entry(ctx, chf, "2026-08", 1900.0)
    await oldest_first_on_chf(user, setup)
    assert field(user, ui.select, "Balance column").value == "Saldo"
    await user.should_see("Month-end balances")
    assert ticked(user) == [
        ("Balance at end of Aug 2026: 1'950.50 CHF", True),
        ("Balance at end of Sep 2026: 7'554.70 CHF", True),
        ("Balance at end of Oct 2026: 7'524.70 CHF", False),
    ]
    await user.should_see("replaces 1'900.00 CHF")
    await user.should_see("the file ends on 3 Oct 2026 — not a month-end balance")
    assert import_button(user).text == "Import 3 Transactions and 2 Balances"  # three rows wait for a category

    put(user, checkboxes(user)[2], True)  # the partial month, ticked by hand
    put(user, checkboxes(user)[0], False)
    assert import_button(user).text == "Import 3 Transactions and 2 Balances"
    put(user, checkboxes(user)[1], False)
    assert import_button(user).text == "Import 3 Transactions and 1 Balance"

    await categorise(user, "01.1", 0, 2, 4, 5)
    await categorise(user, setup["salary"].id, 1, 3)
    assert status(user) == "" and import_button(user).text == "Import 6 Transactions and 1 Balance"
    click(user, import_button(user))
    await user.should_see("Transactions")
    assert notified(user, "Imported 6 transactions · 1 month-end balance recorded")
    assert len(ctx.store.transactions.all()) == 6
    assert wealth.entry_for(ctx, chf, "2026-08").amount == 1900.0  # unticked: kept
    assert wealth.entry_for(ctx, chf, "2026-09") is None
    assert wealth.entry_for(ctx, chf, "2026-10").amount == 7524.7


async def test_balances_are_saved_when_every_line_is_imported_already(user: User, ctx, setup) -> None:
    chf = setup["chf"].id
    parsed = importer.read_file("bank.csv", OLDEST_FIRST.encode())
    done = importer.analyse(ctx, importer.parse_rows(parsed, importer.guess_profile(parsed)), chf)
    for r in done:
        r.category = setup["salary"].id if (r.amount or 0) > 0 else "01.1"
    importer.hold_uncategorized(ctx, done)
    importer.commit(ctx, done, chf)
    wealth.upsert_entry(ctx, chf, "2026-09", 7554.7)

    await oldest_first_on_chf(user, setup)
    await user.should_see("0 new · 6 already imported.")
    await user.should_see("already recorded")
    assert [value for _, value in ticked(user)] == [True, False, False]
    assert import_button(user).text == "Import 0 Transactions and 1 Balance" and import_button(user).enabled
    put(user, checkboxes(user)[0], False)
    assert import_button(user).text == "Import 0 Transactions" and not import_button(user).enabled
    put(user, checkboxes(user)[0], True)

    click(user, import_button(user))
    await user.should_see("Transactions")
    assert notified(user, "Imported 0 transactions · 1 month-end balance recorded")
    assert len(ctx.store.transactions.all()) == 6
    assert wealth.entry_for(ctx, chf, "2026-08").amount == 1950.5


async def test_balances_that_do_not_add_up_are_explained_not_offered(user: User, ctx, setup) -> None:
    broken = OLDEST_FIRST.replace("7'554.70", "7'500.00")
    await user.open("/import")
    await upload(user, "bank.csv", broken.encode())
    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    await user.should_see("Month-end balances")
    await user.should_see("The balances in the file do not follow from its amounts")
    assert checkboxes(user) == []
    assert import_button(user).text == "Import 3 Transactions"  # three rows wait for a category

    put(user, field(user, ui.select, "Balance column"), "")  # no balance column: the section goes
    await user.should_not_see("Month-end balances")


async def test_the_balance_column_is_saved_with_the_settings(user: User, ctx, setup) -> None:
    await oldest_first_on_chf(user, setup)
    name = next(e for e in user.find(kind=ui.input).elements if e.props.get("placeholder") == "Name, e.g. UBS Private")
    put(user, name, "Bank")
    click(user, next(b for b in user.find(kind=ui.button).elements if b.text == "Save Settings"))
    [profile] = importer.load_profiles(ctx.store)
    assert profile.balance_col == "Saldo"


async def test_a_statement_ending_inside_a_month_offers_its_balance_unticked(user: User, ctx, setup) -> None:
    await user.open("/import")
    await upload_xml(user, "s.xml", statement(closing() + entry()))
    await user.should_see("Month-end balances")
    assert ticked(user) == [("Balance at end of Sep 2026: −500.00 CHF", False)]
    await user.should_see("the file ends on 18 Sep 2026 — not a month-end balance")

    put(user, field(user, ui.select, "Account"), setup["eur"].id)  # wrong currency: no balance offered
    await settle()
    await user.should_not_see("Month-end balances")

    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    put(user, checkboxes(user)[0], True)
    assert import_button(user).text == "Import 0 Transactions and 1 Balance"  # the entry waits for a category
    await categorise(user, "01.1", 0)
    assert import_button(user).text == "Import 1 Transaction and 1 Balance"
    click(user, import_button(user))
    await user.should_see("Transactions")
    assert wealth.entry_for(ctx, setup["chf"].id, "2026-09").amount == -500.0
