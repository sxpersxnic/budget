"""The import page with camt.053 / camt.054 statements: no column settings, the account remembered by IBAN."""

from datetime import date

from nicegui import ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from notifications import notified
from test_camt import CAMT053, CAMT054, IBAN
from test_import_page import button, checks, click, detail, field, headline, import_button, problem, put, settle
from test_importer import DE

from budget_app.services import camt


async def upload_xml(user: User, name: str, data: bytes) -> None:
    """Upload as the browser would send an XML file."""
    [element] = user.find(kind=ui.upload).elements
    await element.handle_uploads([SmallFileUpload(name, "text/xml", data)])
    await settle()


def accounts(user: User) -> list:
    return [e for e in user.find(kind=ui.select).elements if e.props.get("label") == "Account"]


async def test_a_statement_needs_no_columns(user: User, ctx, setup) -> None:
    await user.open("/import")
    await user.should_see("camt.054 statement (XML)")
    await upload_xml(user, "september.xml", CAMT053)
    assert headline(user) == "6 transactions from CH93 0076 2011 6238 5295 7, 1 Sep 2026 to 30 Sep 2026"
    await user.should_not_see("The Columns Look Wrong…")
    await user.should_see("It is remembered for this IBAN after the import.")
    assert problem(user) == "Choose the account this file belongs to."
    assert not import_button(user).enabled

    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    assert checks(user) == [
        "Money in and out are the right way round",
        "1 entry is still pending",
        "1 month-end balance to record",
    ]
    await user.should_not_see("That's Wrong")  # a statement says which way the money went
    assert import_button(user).text == "Import 6 Transactions and 1 Balance"

    click(user, import_button(user))
    await user.should_see("Bank is up to date to 30 Sep 2026")
    stored = sorted(ctx.store.transactions.all(), key=lambda tx: (tx.date, tx.amount))
    assert [(tx.date, tx.amount, tx.currency) for tx in stored][:2] == [
        (date(2026, 9, 2), 45.8, "CHF"),
        (date(2026, 9, 25), 7800.0, "CHF"),
    ]
    assert all(tx.import_ref.startswith(f"camt:{IBAN}:") for tx in stored)
    assert camt.remembered_account(ctx.store, IBAN) == setup["chf"].id


async def test_the_next_statement_finds_its_account_and_skips_what_is_imported(user: User, ctx, setup) -> None:
    st = camt.read_statement("september.xml", CAMT053)
    done = st.fresh_rows()
    for r in done:
        r.category = setup["salary"].id if r.amount and r.amount > 0 else "01.1"
    camt.commit(ctx, st, done, setup["chf"].id)

    await user.open("/import")
    await upload_xml(user, "notification.xml", CAMT054)  # same IBAN, other entries
    await user.should_see("Remembered from the last statement of this IBAN.")
    assert field(user, ui.select, "Account").value == setup["chf"].id
    assert "1 line can't be read" in checks(user)
    assert detail(user, "1 line can't be read") == "Amount in EUR, not in the statement's currency. They are left out."
    assert import_button(user).text == "Import 4 Transactions"

    click(user, button(user, "Choose Another File…"))  # handled by the browser
    await upload_xml(user, "september.xml", CAMT053)
    assert checks(user)[0] == "Everything in this file is already in Budget"
    assert import_button(user).text == "Import 0 Transactions and 1 Balance"  # the closing balance is new (F36)


async def test_a_statement_in_another_currency_is_refused(user: User, ctx, setup) -> None:
    await user.open("/import")
    await upload_xml(user, "september.xml", CAMT053)
    put(user, field(user, ui.select, "Account"), setup["eur"].id)
    assert problem(user) == "The statement is in CHF, but “Euro account” is in EUR. Choose an account in CHF."
    assert checks(user) == [] and not import_button(user).enabled

    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    assert problem(user) == "" and import_button(user).enabled


async def test_a_csv_file_after_a_statement_brings_the_columns_back(user: User, ctx, setup) -> None:
    await user.open("/import")
    await upload_xml(user, "september.xml", CAMT053)
    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    await upload_xml(user, "de.csv", DE)
    click(user, button(user, "The Columns Look Wrong…"))
    assert field(user, ui.select, "Date column").value == "Buchungsdatum"
    assert len(accounts(user)) == 1
    assert headline(user).startswith("4 transactions from de.csv")


async def test_bad_xml_files_are_refused(user: User) -> None:
    await user.open("/import")
    await upload_xml(user, "broken.xml", b"<Document><BkToCstmrStmt>")
    assert notified(user, "The file is not valid XML (line 1)")
    await upload_xml(user, "payment.xml", b"<Document><CstmrCdtTrfInitn/></Document>")
    assert notified(user, "The XML file is not a camt.053 or camt.054 bank statement")
    await user.should_see("Bring in a bank file")
