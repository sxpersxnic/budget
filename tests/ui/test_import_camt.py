"""The import page with camt.053 / camt.054 statements: file → review → import, no column step."""

from datetime import date

from nicegui import ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from test_camt import CAMT053, CAMT054, IBAN
from test_import_page import button, categorise, click, field, import_button, put, rows, settle, status
from test_importer import DE

from budget_app.services import camt


async def upload_xml(user: User, name: str, data: bytes) -> None:
    """Upload as the browser would send an XML file."""
    [element] = user.find(kind=ui.upload).elements
    await element.handle_uploads([SmallFileUpload(name, "text/xml", data)])
    await settle()


def accounts(user: User) -> list:
    return [e for e in user.find(kind=ui.select).elements if e.props.get("label") == "Account"]


async def test_a_statement_goes_straight_to_review(user: User, ctx, setup) -> None:
    await user.open("/import")
    await user.should_see("camt.054 statement (XML)")
    await upload_xml(user, "september.xml", CAMT053)
    await user.should_see(
        "september.xml · camt.053.001.04 · CH93 0076 2011 6238 5295 7 · 1 Sep 2026 – 30 Sep 2026 · 6 entries"
        " · 1 pending entry left out"
    )
    await user.should_not_see("2 · Columns")
    await user.should_see("2 · Review")
    await user.should_see("The account is remembered for this IBAN after the import.")
    assert [(r["date"], r["amount"], r["status"]) for r in rows(user)][:3] == [
        ("2026-09-02", -45.8, "New"),
        ("2026-09-25", 7800.0, "New"),
        ("2026-09-26", -2150.0, "New"),
    ]
    assert status(user) == "Choose the account this file belongs to."
    assert not import_button(user).enabled

    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    await user.should_see("6 new.")
    await categorise(user, "04.1", 0, 2, 3, 4, 5)
    await categorise(user, setup["salary"].id, 1)
    assert status(user) == "" and import_button(user).text == "Import 6 Transactions"

    click(user, import_button(user))
    await user.should_see("Transactions")
    assert user.notify.contains("Imported 6 transactions")
    stored = sorted(ctx.store.transactions.all(), key=lambda tx: (tx.date, tx.amount))
    assert [(tx.date, tx.amount, tx.currency) for tx in stored][:2] == [
        (date(2026, 9, 2), 45.8, "CHF"),
        (date(2026, 9, 25), 7800.0, "CHF"),
    ]
    assert all(tx.import_ref.startswith(f"camt:{IBAN}:") for tx in stored)
    assert camt.remembered_account(ctx.store.data_dir, IBAN) == setup["chf"].id


async def test_the_next_statement_finds_its_account_and_skips_what_is_imported(user: User, ctx, setup) -> None:
    st = camt.read_statement("september.xml", CAMT053)
    done = st.fresh_rows()
    for r in done:
        r.category = setup["salary"].id if r.amount and r.amount > 0 else "01.1"
    camt.commit(ctx, st, done, setup["chf"].id)

    await user.open("/import")
    await upload_xml(user, "notification.xml", CAMT054)  # same IBAN, other entries
    await user.should_see("Account remembered from the last import of this IBAN.")
    assert field(user, ui.select, "Account").value == setup["chf"].id
    await user.should_see("4 new · 1 unreadable.")
    assert rows(user)[3]["status"] == "Amount in EUR, not in the statement's currency"

    await upload_xml(user, "september.xml", CAMT053)
    await user.should_see("0 new · 6 already imported.")
    assert not import_button(user).enabled


async def test_a_statement_in_another_currency_is_refused(user: User, ctx, setup) -> None:
    await user.open("/import")
    await upload_xml(user, "september.xml", CAMT053)
    put(user, field(user, ui.select, "Account"), setup["eur"].id)
    message = "The statement is in CHF, but “Euro account” is in EUR. Choose an account in CHF."
    assert user.notify.contains(message)
    assert status(user) == message
    assert not import_button(user).enabled
    assert all(r["amount"] for r in rows(user))  # still shown, so the user sees what the file holds

    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    assert status(user).startswith("6 rows need a category")


async def test_a_csv_file_after_a_statement_brings_the_columns_back(user: User, ctx, setup) -> None:
    await user.open("/import")
    await upload_xml(user, "september.xml", CAMT053)
    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    await upload_xml(user, "de.csv", DE)
    await user.should_see("2 · Columns")
    await user.should_see("3 · Review")
    await user.should_not_see("2 · Review")
    assert len(accounts(user)) == 1  # the one account field is back in the columns step
    assert field(user, ui.select, "Date column").value == "Buchungsdatum"
    click(user, button(user, "Save Settings"))  # the CSV steps work as before
    assert user.notify.contains("Enter a name for these settings")


async def test_bad_xml_files_are_refused(user: User) -> None:
    await user.open("/import")
    await upload_xml(user, "broken.xml", b"<Document><BkToCstmrStmt>")
    assert user.notify.contains("The file is not valid XML (line 1)")
    await upload_xml(user, "payment.xml", b"<Document><CstmrCdtTrfInitn/></Document>")
    assert user.notify.contains("The XML file is not a camt.053 or camt.054 bank statement")
    await user.should_not_see("2 · Review")
    await user.should_not_see("3 · Review")
