"""The CSV import page: file → columns → review → import, with saved bank settings and duplicates."""

import asyncio
import re
from datetime import date

from nicegui import ElementFilter, ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified
from test_importer import DE, UBS

from budget_app.services import importer
from budget_app.services import transactions as tx_svc
from budget_app.services.transactions import TransactionInput

SELECTED = re.compile(r".*getSelectedRows")


# ------------------------------------------------------------------ helpers
async def settle() -> None:
    """Let async handlers (uploads, selection requests) finish."""
    for _ in range(5):
        await asyncio.sleep(0.01)


def field(user: User, kind: type, label: str):
    found = [e for e in user.find(kind=kind).elements if e.props.get("label") == label]
    assert len(found) == 1, f"expected one {kind.__name__} labelled {label!r}, found {len(found)}"
    return found[0]


def put(user: User, element, value) -> None:
    with user:
        element.value = value


def button(user: User, text: str) -> ui.button:
    found = [b for b in user.find(kind=ui.button).elements if b.text.startswith(text)]
    assert found, f"no visible button {text!r}"
    return max(found, key=lambda b: b.id)


def click(user: User, element) -> None:
    UserInteraction(user, {element}, None).click()


def import_button(user: User) -> ui.button:
    return next(b for b in user.find(kind=ui.button).elements if b.text.startswith("Import ") and "…" not in b.text)


def rows(user: User) -> list[dict]:
    [grid] = user.find(kind=ui.aggrid).elements
    return grid.options["rowData"]


def labels(user: User) -> list[str]:
    return [e.text for e in user.find(kind=ui.label).elements if e.text]


def status(user: User) -> str:
    btn = import_button(user)
    return next(c.text for c in btn.parent_slot.children if isinstance(c, ui.label))


async def upload(user: User, name: str, data: bytes) -> None:
    [element] = user.find(kind=ui.upload).elements
    await element.handle_uploads([SmallFileUpload(name, "text/csv", data)])
    await settle()


async def select(user: User, *indexes: int) -> None:
    user.javascript_rules[SELECTED] = lambda _: [{"id": str(i)} for i in indexes]
    [grid] = user.find(kind=ui.aggrid).elements
    UserInteraction(user, {grid}, None).trigger("selectionChanged")
    await user.should_see(f"{len(indexes)} selected")


async def categorise(user: User, category: str, *indexes: int) -> None:
    await select(user, *indexes)
    put(user, field(user, ui.select, "Category"), category)
    click(user, button(user, "Apply"))
    await settle()


async def de_file_on_euro_account(user: User, setup) -> None:
    await user.open("/import")
    await upload(user, "de.csv", DE)
    put(user, field(user, ui.select, "Account"), setup["eur"].id)


# ------------------------------------------------------------------ the whole flow
async def test_import_a_file_in_three_steps(user: User, ctx, setup) -> None:
    await user.open("/import")
    await user.should_see("1 · File")
    await user.should_not_see("3 · Review")

    await upload(user, "de.csv", DE)
    await user.should_see("de.csv · 4 lines")
    await user.should_see("2 · Columns")
    assert field(user, ui.select, "Date column").value == "Buchungsdatum"
    assert field(user, ui.select, "Amount").value == "Betrag"
    assert field(user, ui.select, "Description").value == ["Verwendungszweck"]
    assert field(user, ui.select, "Separator").value == ";"
    assert [(r["date"], r["amount"], r["status"]) for r in rows(user)] == [
        ("2026-09-15", -12.3, "New"),
        ("2026-09-15", -12.3, "New"),
        ("2026-09-16", 1234.5, "New"),
        ("", None, "No valid date"),
    ]
    assert status(user) == "Choose the account this file belongs to."
    assert not import_button(user).enabled

    put(user, field(user, ui.select, "Account"), setup["eur"].id)
    await user.should_see("3 new · 1 unreadable. Duplicates and unreadable lines are left out")
    assert status(user) == "3 rows need a category: select rows, choose a category and click Apply."
    assert import_button(user).text == "Import 3 Transactions" and not import_button(user).enabled

    await categorise(user, "01.1", 0, 1)
    assert status(user) == "1 row needs a category: select rows, choose a category and click Apply."
    await categorise(user, setup["salary"].id, 2)
    assert [r["category"] for r in rows(user)][:3] == ["Groceries · 01.1", "Groceries · 01.1", "Income · Salary"]
    assert status(user) == "" and import_button(user).enabled

    click(user, import_button(user))
    await user.should_see("Transactions")
    assert notified(user, "Imported 3 transactions")
    stored = sorted(ctx.store.transactions.all(), key=lambda tx: (tx.date, tx.amount))
    assert [(tx.date, tx.type, tx.amount, tx.currency, tx.category, tx.comment) for tx in stored] == [
        (date(2026, 9, 15), "expenditure", 12.3, "EUR", "01.1", "Coop Pronto 1234"),
        (date(2026, 9, 15), "expenditure", 12.3, "EUR", "01.1", "Coop Pronto 1234"),
        (date(2026, 9, 16), "income", 1234.5, "EUR", setup["salary"].id, "Rückerstattung"),
    ]
    assert all(tx.import_ref.startswith("csv:") for tx in stored)
    assert stored[0].from_account == setup["eur"].id and stored[2].to_account == setup["eur"].id


async def test_reimporting_the_same_file_skips_every_line(user: User, ctx, setup) -> None:
    parsed = importer.read_file("de.csv", DE)
    rows_ = importer.analyse(ctx, importer.parse_rows(parsed, importer.guess_profile(parsed)), setup["eur"].id)
    for r in rows_[:2]:
        r.category = "01.1"
    rows_[2].category = setup["salary"].id
    importer.commit(ctx, rows_, setup["eur"].id)

    await de_file_on_euro_account(user, setup)
    await user.should_see("0 new · 3 already imported · 1 unreadable.")
    assert [r["status"] for r in rows(user)] == ["Already imported"] * 3 + ["No valid date"]
    assert [r["include"] for r in rows(user)] == [False] * 4
    assert import_button(user).text == "Import 0 Transactions" and not import_button(user).enabled
    # categories are suggested from what was imported before
    assert rows(user)[0]["category"] == "Groceries · 01.1 (suggested)"


async def test_a_manual_entry_marks_a_possible_duplicate(user: User, ctx, setup) -> None:
    tx_svc.save(
        ctx,
        TransactionInput(
            date=date(2026, 9, 15),
            amount=12.3,
            currency="EUR",
            category="01.1",
            from_account=setup["eur"].id,
        ),
    )
    await de_file_on_euro_account(user, setup)
    await user.should_see("2 new · 1 possible duplicate · 1 unreadable.")
    assert [r["status"] for r in rows(user)][:2] == ["Possible duplicate", "New"]


# ------------------------------------------------------------------ review actions
async def test_categories_only_fit_the_matching_direction(user: User, ctx, setup) -> None:
    await de_file_on_euro_account(user, setup)
    await select(user, 0, 2)
    click(user, button(user, "Apply"))
    await settle()
    assert notified(user, "Choose a category first")

    put(user, field(user, ui.select, "Category"), "01.1")
    click(user, button(user, "Apply"))
    await settle()
    assert notified(user, "1 row skipped: that category is for spending only")
    assert [r["category"] for r in rows(user)][:3] == ["Groceries · 01.1", "", ""]

    await categorise(user, setup["salary"].id, 0, 1, 2)
    assert notified(user, "2 rows skipped: that category is for money coming in only")
    assert [r["category"] for r in rows(user)][:3] == ["Groceries · 01.1", "", "Income · Salary"]


async def test_rows_can_be_excluded_and_included(user: User, ctx, setup) -> None:
    await de_file_on_euro_account(user, setup)
    await categorise(user, "01.1", 0, 1)
    await categorise(user, setup["salary"].id, 2)
    assert import_button(user).text == "Import 3 Transactions"

    await select(user, 1, 2)
    click(user, button(user, "Exclude"))
    await settle()
    assert [r["include"] for r in rows(user)] == [True, False, False, False]
    assert import_button(user).text == "Import 1 Transaction"

    await select(user, 1, 3)  # an unreadable line cannot be included
    click(user, button(user, "Include"))
    await settle()
    assert [r["include"] for r in rows(user)] == [True, True, False, False]

    # the checkbox in the grid
    [grid] = user.find(kind=ui.aggrid).elements
    grid_events = UserInteraction(user, {grid}, None)
    grid_events.trigger("cellValueChanged", {"data": {"id": "0"}, "colId": "include", "newValue": False})
    grid_events.trigger("cellValueChanged", {"data": {"id": "3"}, "colId": "include", "newValue": True})
    grid_events.trigger("cellValueChanged", {"data": {"id": "1"}, "colId": "text", "newValue": "edited"})
    assert import_button(user).text == "Import 1 Transaction"

    click(user, import_button(user))
    assert notified(user, "Imported 1 transaction")
    [tx] = ctx.store.transactions.all()
    assert tx.amount == 12.3 and tx.date == date(2026, 9, 15)


async def test_import_reports_a_failure_and_saves_nothing(user: User, ctx, setup) -> None:
    await de_file_on_euro_account(user, setup)
    await categorise(user, "01.1", 0, 1)
    await categorise(user, setup["salary"].id, 2)
    ctx.store.accounts.delete([setup["eur"].id])  # removed in another window meanwhile
    click(user, import_button(user))
    assert notified(user, "Choose the account the file belongs to")
    assert ctx.store.transactions.all() == []


# ------------------------------------------------------------------ columns
async def test_debit_and_credit_columns_and_inverted_signs(user: User, ctx, setup) -> None:
    await user.open("/import")
    await upload(user, "ubs.csv", UBS)
    await user.should_see("ubs.csv · 3 lines")
    mode = next(e for e in user.find(kind=ui.toggle).elements if "split" in e.options)
    assert mode.value == "split"
    assert field(user, ui.select, "Debit (money out)").value == "Belastung"
    assert field(user, ui.select, "Credit (money in)").value == "Gutschrift"
    assert field(user, ui.number, "Header on line").value == 4
    assert [r["amount"] for r in rows(user)] == [-45.8, 7800.0, -2150.0]

    put(user, next(e for e in user.find(kind=ui.switch).elements if "Positive amounts" in e.text), True)
    assert [r["amount"] for r in rows(user)] == [45.8, -7800.0, 2150.0]

    put(user, mode, "single")
    assert not [e for e in user.find(kind=ui.select).elements if e.props.get("label") == "Debit (money out)"]
    put(user, field(user, ui.select, "Amount"), "Saldo")
    assert [r["amount"] for r in rows(user)] == [-8154.2, -15954.2, -13804.2]


async def test_separator_and_header_line_reread_the_file(user: User, ctx, setup) -> None:
    await user.open("/import")
    await upload(user, "ubs.csv", UBS)
    put(user, field(user, ui.select, "Account"), setup["chf"].id)

    put(user, field(user, ui.number, "Header on line"), 1)
    assert field(user, ui.select, "Date column").options.get("Kontonummer:") == "Kontonummer:"
    assert all(r["status"] == "No valid date" for r in rows(user))
    assert field(user, ui.select, "Account").value == setup["chf"].id  # the account stays

    put(user, field(user, ui.number, "Header on line"), 4)
    assert field(user, ui.select, "Date column").value == "Abschlussdatum"
    assert len(rows(user)) == 3

    put(user, field(user, ui.select, "Separator"), ",")
    assert all(r["status"] == "No valid date" for r in rows(user))  # nothing splits on commas


async def test_saved_settings_recognise_the_next_file(user: User, ctx, setup) -> None:
    await user.open("/import")
    await upload(user, "ubs.csv", UBS)
    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    put(user, field(user, ui.select, "Date format"), "%d.%m.%Y")
    click(user, button(user, "Save Settings"))
    assert notified(user, "Enter a name for these settings")
    assert importer.load_profiles(ctx.store.data_dir) == []

    name = next(e for e in user.find(kind=ui.input).elements if e.props.get("placeholder") == "Name, e.g. UBS Private")
    put(user, name, "  UBS Private ")
    click(user, button(user, "Save Settings"))
    assert notified(user, "Saved “UBS Private”")
    [profile] = importer.load_profiles(ctx.store.data_dir)
    assert (profile.name, profile.account_id, profile.debit_col, profile.header_row) == (
        "UBS Private",
        setup["chf"].id,
        "Belastung",
        3,
    )
    assert field(user, ui.select, "Saved settings").value == "UBS Private"

    await user.open("/import")
    await upload(user, "ubs-october.csv", UBS)
    await user.should_see("ubs-october.csv · 3 lines · recognised as “UBS Private”")
    assert field(user, ui.select, "Saved settings").value == "UBS Private"
    assert field(user, ui.select, "Account").value == setup["chf"].id
    await user.should_see("3 new.")


async def test_choosing_saved_settings_rereads_the_file(user: User, ctx, setup) -> None:
    parsed = importer.read_file("ubs.csv", UBS)
    profile = importer.guess_profile(parsed)
    profile.name = "UBS"
    importer.save_profile(ctx.store.data_dir, profile)
    other = importer.ImportProfile(name="Other bank", delimiter=",", signature="other")
    importer.save_profile(ctx.store.data_dir, other)

    await user.open("/import")
    await upload(user, "ubs.csv", UBS)
    saved = field(user, ui.select, "Saved settings")
    assert saved.value == "UBS" and set(saved.options) == {"UBS", "Other bank"}
    put(user, field(user, ui.select, "Separator"), ",")
    assert all(r["status"] == "No valid date" for r in rows(user))

    put(user, saved, None)
    put(user, saved, "UBS")  # back to the saved separator and header line
    assert field(user, ui.select, "Separator").value == ";"
    assert field(user, ui.select, "Date column").value == "Abschlussdatum"
    assert [r["amount"] for r in rows(user)] == [-45.8, 7800.0, -2150.0]


async def test_save_settings_without_a_file_does_nothing(user: User, ctx) -> None:
    await user.open("/import")
    with user:
        [hidden] = [b for b in ElementFilter(kind=ui.button, local_scope=False) if b.text == "Save Settings"]
    click(user, hidden)
    assert user.notify.messages == []
    assert importer.load_profiles(ctx.store.data_dir) == []


# ------------------------------------------------------------------ bad files
async def test_an_empty_file_is_refused(user: User) -> None:
    await user.open("/import")
    await upload(user, "empty.csv", b"\n\n")
    assert notified(user, "The file is empty")
    await user.should_not_see("2 · Columns")


async def test_the_choose_button_opens_the_file_dialog(user: User) -> None:
    await user.open("/import")
    click(user, button(user, "Choose File…"))  # handled by the browser; must not fail
    await user.should_see("1 · File")
