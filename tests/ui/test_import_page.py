"""The import page (decision 48): your file in one sentence with plain checks, one Import button, then a few
questions about the payees Budget could not place."""

import asyncio
from datetime import date

from nicegui import ElementFilter, ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction
from notifications import notified
from test_importer import DE, UBS

from budget_app.domain.models import TX_INCOME
from budget_app.services import importer, sorting
from budget_app.services import transactions as tx_svc
from budget_app.services.transactions import TransactionInput


# ------------------------------------------------------------------ helpers
async def settle() -> None:
    """Let async handlers (uploads) finish."""
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


async def upload(user: User, name: str, data: bytes) -> None:
    [element] = user.find(kind=ui.upload).elements
    await element.handle_uploads([SmallFileUpload(name, "text/csv", data)])
    await settle()


def import_button(user: User) -> ui.button:
    return next(b for b in user.find(kind=ui.button).elements if b.text.startswith("Import ") and "…" not in b.text)


def headline(user: User) -> str:
    return next(e.text for e in user.find(kind=ui.label).elements if "import-headline" in e.classes)


def checks(user: User) -> list[str]:
    """The checks of the file, as their titles."""
    with user:
        return [
            label.text
            for box in ElementFilter(kind=ui.element, local_scope=False)
            if "import-check" in box.classes
            for label in box.descendants()
            if isinstance(label, ui.label) and "font-semibold" in label.classes
        ]


def detail(user: User, title: str) -> str:
    """The line under a check's title."""
    with user:
        for box in ElementFilter(kind=ui.element, local_scope=False):
            labels = (
                [e.text for e in box.descendants() if isinstance(e, ui.label)] if "import-check" in box.classes else []
            )
            if title in labels:
                return labels[labels.index(title) + 1]
    raise AssertionError(f"no check {title!r}")


def problem(user: User) -> str:
    return next(e.text for e in user.find(kind=ui.label).elements if "import-problem" in e.classes)


async def de_file_on_euro_account(user: User, setup) -> None:
    await user.open("/import")
    await upload(user, "de.csv", DE)
    put(user, field(user, ui.select, "Account"), setup["eur"].id)


# ------------------------------------------------------------------ the whole flow
async def test_import_a_file_and_answer_the_one_question(user: User, ctx, setup) -> None:
    await user.open("/import")
    await user.should_see("Bring in a bank file")
    await upload(user, "de.csv", DE)
    assert headline(user) == "4 transactions from de.csv, 15 Sep 2026 to 16 Sep 2026"
    assert problem(user) == "Choose the account this file belongs to."
    assert checks(user) == [] and not import_button(user).enabled

    put(user, field(user, ui.select, "Account"), setup["eur"].id)
    assert problem(user) == ""
    assert checks(user) == ["Money in and out are the right way round", "1 line can't be read"]
    assert "+1'234.50" in detail(user, "Money in and out are the right way round")
    assert detail(user, "1 line can't be read") == "No valid date. They are left out."
    assert import_button(user).text == "Import 3 Transactions" and import_button(user).enabled

    click(user, import_button(user))  # the refund has no category: it is imported all the same (decision 48)
    await user.should_see("Euro account is up to date to 16 Sep 2026")
    await user.should_see("3 transactions imported. Undo the whole import from History at any time.")
    await user.should_see("1 payee Budget doesn't know yet")
    await user.should_see("What is this money?")
    assert len(ctx.store.transactions.all()) == 3
    assert [g.name for g in sorting.groups(ctx)] == ["Rückerstattung"]

    click(user, button(user, "Salary"))
    await settle()
    assert notified(user, "1 transaction is now Salary")
    refund = next(tx for tx in ctx.store.transactions.all() if tx.type == TX_INCOME)
    assert refund.category == setup["salary"].id and sorting.unsorted(ctx) == []
    await user.should_see("2 sorted only by a word in the name")  # Coop → groceries, folded


async def test_a_file_read_the_wrong_way_round_is_swapped(user: User, ctx, setup) -> None:
    await de_file_on_euro_account(user, setup)
    signs = "Money in and out are the right way round"
    assert detail(user, signs).startswith("Rückerstattung +1'234.50")
    click(user, button(user, "That's Wrong"))
    assert "Rückerstattung −1'234.50" in detail(user, signs)  # now spending; the switch below says so too
    with user:  # in the folded column settings
        assert next(e for e in ElementFilter(kind=ui.switch, local_scope=False) if "Positive amounts" in e.text).value


async def test_what_is_already_in_budget_is_skipped_and_can_be_imported_anyway(user: User, ctx, setup) -> None:
    typed = TransactionInput(
        date=date(2026, 9, 2), amount=45.80, currency="CHF", category="01.1", from_account=setup["chf"].id
    )
    tx_svc.save(ctx, typed)
    await user.open("/import")
    await upload(user, "ubs.csv", UBS)
    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    assert "1 is already in Budget" in checks(user)
    assert import_button(user).text == "Import 2 Transactions"

    click(user, button(user, "Show Them"))
    await user.should_see("You have: Groceries")
    put(user, next(e for e in user.find(kind=ui.checkbox).elements if e.text == "Import anyway"), True)
    assert import_button(user).text == "Import 3 Transactions"


async def test_reimporting_the_same_file_imports_nothing(user: User, ctx, setup) -> None:
    parsed = importer.read_file("de.csv", DE)
    rows = importer.analyse(ctx, importer.parse_rows(parsed, importer.guess_profile(parsed)), setup["eur"].id)
    importer.commit(ctx, rows, setup["eur"].id)

    await de_file_on_euro_account(user, setup)
    assert checks(user)[0] == "Everything in this file is already in Budget"
    assert import_button(user).text == "Import 0 Transactions" and not import_button(user).enabled


async def test_import_reports_a_failure_and_saves_nothing(user: User, ctx, setup) -> None:
    await de_file_on_euro_account(user, setup)
    ctx.store.accounts.delete([setup["eur"].id])  # removed in another window meanwhile
    click(user, import_button(user))
    assert notified(user, "Choose the account the file belongs to")
    assert ctx.store.transactions.all() == []


# ------------------------------------------------------------------ columns
async def test_the_columns_stay_folded_until_asked_for(user: User, ctx, setup) -> None:
    await user.open("/import")
    await upload(user, "ubs.csv", UBS)
    await user.should_not_see("Date column")
    click(user, button(user, "The Columns Look Wrong…"))
    await user.should_see("Date column")


async def test_debit_and_credit_columns_and_inverted_signs(user: User, ctx, setup) -> None:
    await user.open("/import")
    await upload(user, "ubs.csv", UBS)
    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    click(user, button(user, "The Columns Look Wrong…"))
    mode = next(e for e in user.find(kind=ui.toggle).elements if "split" in e.options)
    assert mode.value == "split"
    assert field(user, ui.select, "Debit (money out)").value == "Belastung"
    assert field(user, ui.select, "Credit (money in)").value == "Gutschrift"
    assert field(user, ui.number, "Header on line").value == 4
    signs = "Money in and out are the right way round"
    assert detail(user, signs).endswith("+7'800.00 · Migros −45.80")

    put(user, mode, "single")
    assert not [e for e in user.find(kind=ui.select).elements if e.props.get("label") == "Debit (money out)"]
    put(user, field(user, ui.select, "Amount"), "Saldo")
    assert detail(user, signs) == "Firma AG +15'954.20"  # the balances: all of it reads as money coming in


async def test_separator_and_header_line_reread_the_file(user: User, ctx, setup) -> None:
    await user.open("/import")
    await upload(user, "ubs.csv", UBS)
    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    click(user, button(user, "The Columns Look Wrong…"))

    put(user, field(user, ui.number, "Header on line"), 1)
    assert field(user, ui.select, "Date column").options.get("Kontonummer:") == "Kontonummer:"
    assert "5 lines can't be read" in checks(user)
    assert field(user, ui.select, "Account").value == setup["chf"].id  # the account stays

    put(user, field(user, ui.number, "Header on line"), 4)
    assert field(user, ui.select, "Date column").value == "Abschlussdatum"
    assert import_button(user).text == "Import 3 Transactions"


async def test_saved_settings_recognise_the_next_file(user: User, ctx, setup) -> None:
    await user.open("/import")
    await upload(user, "ubs.csv", UBS)
    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    click(user, button(user, "The Columns Look Wrong…"))
    click(user, button(user, "Save Settings"))
    assert notified(user, "Enter a name for these settings")
    assert importer.load_profiles(ctx.store) == []

    name = next(e for e in user.find(kind=ui.input).elements if e.props.get("placeholder") == "Name, e.g. UBS Private")
    put(user, name, "  UBS Private ")
    click(user, button(user, "Save Settings"))
    assert notified(user, "Saved “UBS Private”")
    [profile] = importer.load_profiles(ctx.store)
    assert (profile.name, profile.account_id, profile.debit_col, profile.header_row) == (
        "UBS Private",
        setup["chf"].id,
        "Belastung",
        3,
    )

    await user.open("/import")
    await upload(user, "ubs-october.csv", UBS)
    assert headline(user) == "3 transactions from UBS Private, 2 Sep 2026 to 26 Sep 2026"
    assert field(user, ui.select, "Account").value == setup["chf"].id
    await user.should_see("The account of this bank's last file.")
    assert import_button(user).text == "Import 3 Transactions"


async def test_choosing_saved_settings_rereads_the_file(user: User, ctx, setup) -> None:
    parsed = importer.read_file("ubs.csv", UBS)
    profile = importer.guess_profile(parsed)
    profile.name = "UBS"
    importer.save_profile(ctx.store, profile)
    other = importer.ImportProfile(name="Other bank", delimiter=",", signature="other")
    importer.save_profile(ctx.store, other)

    await user.open("/import")
    await upload(user, "ubs.csv", UBS)
    put(user, field(user, ui.select, "Account"), setup["chf"].id)
    click(user, button(user, "The Columns Look Wrong…"))
    saved = field(user, ui.select, "Saved settings")
    assert saved.value == "UBS" and set(saved.options) == {"UBS", "Other bank"}
    put(user, field(user, ui.select, "Separator"), ",")
    assert "3 lines can't be read" in checks(user)  # nothing splits on commas

    put(user, saved, None)
    put(user, saved, "UBS")  # back to the saved separator and header line
    assert field(user, ui.select, "Separator").value == ";"
    assert field(user, ui.select, "Date column").value == "Abschlussdatum"
    assert import_button(user).text == "Import 3 Transactions"


async def test_save_settings_without_a_file_does_nothing(user: User, ctx) -> None:
    await user.open("/import")
    with user:
        [hidden] = [b for b in ElementFilter(kind=ui.button, local_scope=False) if b.text == "Save Settings"]
    click(user, hidden)
    assert user.notify.messages == []
    assert importer.load_profiles(ctx.store) == []


# ------------------------------------------------------------------ bad files
async def test_an_empty_file_is_refused(user: User) -> None:
    await user.open("/import")
    await upload(user, "empty.csv", b"\n\n")
    assert notified(user, "The file is empty")
    await user.should_see("Bring in a bank file")


async def test_the_choose_button_opens_the_file_dialog(user: User) -> None:
    await user.open("/import")
    choose = button(user, "Choose File…")
    click(user, choose)  # handled by the browser; must not fail
    await user.should_see("Bring in a bank file")
    # in the browser, during the click: a round trip to the server first loses the right to open the dialog (WebKit)
    [listener] = [lst for lst in choose._event_listeners.values() if lst.type == "click"]
    assert "'pickFiles'" in listener.js_handler and listener.handler is None
