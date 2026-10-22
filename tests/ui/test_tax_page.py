"""The Tax Summary page (F20): sections with their totals and rows, the year picker, the sheet that links tags to
sections, opening a row and its proof, and the PDF, CSV and ZIP exports."""

import asyncio
import io
import zipfile
from datetime import date

import pandas as pd
from nicegui import app, ui
from nicegui.testing import User
from notifications import notified
from test_bills_page import Window
from test_data_io import downloaded, menu_item
from test_payees_page import click, icon_buttons, newest, put

from budget_app.domain.models import ASSET_SECURITIES, TRADE_DIVIDEND
from budget_app.services import accounts as acc_svc
from budget_app.services import attachments as att_svc
from budget_app.services import investments as inv_svc
from budget_app.services import tags as tag_svc
from budget_app.services import tax as tax_svc
from budget_app.services import transactions as tx_svc
from budget_app.services.investments import TradeInput
from budget_app.services.transactions import TransactionInput
from budget_app.ui import layout
from budget_app.ui.pages import tax as tax_page
from budget_app.ui.widgets.tags import TagSelect

PDF = b"%PDF-1.4\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"
LAST = date.today().year - 1  # the year the page opens with


def spend(ctx, setup, amount, category, day=None, **kw):
    return tx_svc.save(
        ctx,
        TransactionInput(
            date=day or date(LAST, 3, 10),
            type="expenditure",
            amount=amount,
            currency="CHF",
            category=category,
            from_account=setup["chf"].id,
            **kw,
        ),
    )


async def until(check) -> None:
    """Wait for something the page does in the background (a ZIP is built in a thread)."""
    for _ in range(200):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("it did not happen")


def labels(user: User) -> list[str]:
    return [lb.text for lb in sorted(user.find(kind=ui.label).elements, key=lambda e: e.id)]


async def test_the_page_lists_the_sections_of_last_year(user: User, ctx, setup) -> None:
    doctor = spend(ctx, setup, 240, "06.2", payee="Dr. Muster")
    att_svc.add(ctx, doctor.id, "invoice.pdf", PDF)
    spend(ctx, setup, 85, "07.3", payee="ZVV")
    spend(ctx, setup, 99, "06", day=date(LAST + 1, 1, 5))  # the next year
    await user.open("/tax")
    assert layout.NAV[-1] == ("/tax", "Tax Summary", "o_account_balance")
    await user.should_see("Tax Summary")
    await user.should_see("Cantonal rules differ", kind=ui.label)
    await user.should_see("Sections can overlap", kind=ui.label)
    await user.should_see(f"{LAST} · amounts in CHF")
    shown = labels(user)
    assert "Medical and dental costs" in shown and "240.00 CHF" in shown and "1 row · 1 file" in shown
    assert "Professional expenses" in shown and "85.00 CHF" in shown
    assert shown.count(f"Nothing in {LAST}") == 2 * 7  # the header and the body of each empty section
    assert "Dr. Muster" in shown and f"10 Mar {LAST} · 06.2 Outpatient care services" in shown
    links = [ln for ln in user.find(kind=ui.link).elements if "/attachment/" in ln.props.get("href", "")]
    assert [ln.props["href"] for ln in links] == [f"/attachment/{att_svc.list_for(ctx, doctor.id)[0].id}"]

    year = newest(user, ui.select, "Year")
    assert year.value == LAST and LAST + 1 in year.options
    put(user, year, LAST + 1)
    await user.should_see(f"{LAST + 1} · amounts in CHF")
    assert "99.00 CHF" in labels(user)


async def test_sections_open_and_remember_it(user: User, ctx, setup) -> None:
    spend(ctx, setup, 240, "06.2")
    await user.open("/tax")
    medical = sorted(user.find(kind=ui.expansion).elements, key=lambda e: e.id)[3]
    put(user, medical, True)
    put(user, newest(user, ui.select, "Year"), LAST + 1)
    put(user, newest(user, ui.select, "Year"), LAST)
    expansions = sorted(user.find(kind=ui.expansion).elements, key=lambda e: e.id)[-9:]
    assert [e.value for e in expansions] == [False, False, False, True, False, False, False, False, False]
    put(user, expansions[3], False)
    put(user, newest(user, ui.select, "Year"), LAST + 1)
    assert not any(e.value for e in sorted(user.find(kind=ui.expansion).elements, key=lambda e: e.id)[-9:])


async def test_linking_tags_in_the_sheet(user: User, ctx, setup, monkeypatch) -> None:
    work = tag_svc.add(ctx, "Work", "teal")
    spend(ctx, setup, 129, "13.2.9.1", tags=[work.id], payee="Digitec")
    glasses = spend(ctx, setup, 300, "06.1.3.1", tags=[work.id], payee="Optiker")
    await user.open("/tax")
    click(user, newest(user, ui.button, text="Sections…"))
    await user.should_see("Tax Sections")
    await user.should_see("Categories: 06 Health, 14.2 Health, 15.2 Health")
    await user.should_see("Transfers to pillar 3a accounts")
    await user.should_see("Dividends with withholding tax in CHF accounts (Investments)")
    await user.should_see("Dividends (Investments) and income in the categories Dividends and Interest")
    fields = sorted((f for f in user.find(kind=TagSelect).elements), key=lambda e: e.id)[-9:]
    put(user, fields[0], [work.id])
    put(user, fields[3], [work.id])
    click(user, newest(user, ui.button, text="Save"))
    assert notified(user, "“Work” can belong to one section only")
    put(user, fields[3], [])
    click(user, newest(user, ui.button, text="Save"))
    assert notified(user, "Saved the tax sections")
    assert tax_svc.links(ctx)["professional"] == [work.id]
    await user.should_see(f"{LAST} · amounts in CHF · 1 transaction is in more than one section")
    shown = labels(user)
    assert "Also by tag" in shown and "429.00 CHF" in shown
    chips = [lb for lb in user.find(kind=ui.label).elements if lb.text == "Work" and "tag-chip" in lb.classes]
    assert len(chips) == 3  # the section's link, and each row it brought in

    opened = []
    monkeypatch.setattr(tax_page, "open_transaction_sheet", lambda ctx, refresh, editing: opened.append(editing.id))
    click(user, icon_buttons(user, "o_open_in_new")[-1])
    assert opened == [glasses.id]


async def test_exports(user: User, ctx, setup) -> None:
    doctor = spend(ctx, setup, 240, "06.2", payee="Dr. Muster")
    att_svc.add(ctx, doctor.id, "invoice.pdf", PDF)
    await user.open("/tax")
    await menu_item(user, "Export as CSV…")
    df = pd.read_csv(io.BytesIO((await downloaded(user)).content))
    assert list(df["Payee"]) == ["Dr. Muster"] and list(df["Proof"]) == ["invoice.pdf"]
    await menu_item(user, "Export as PDF…")
    assert (await downloaded(user)).content.startswith(b"%PDF")
    await menu_item(user, "Proofs as ZIP…")
    await until(lambda: len(user.download.http_responses) == 3)
    names = zipfile.ZipFile(io.BytesIO(user.download.http_responses[-1].content)).namelist()
    assert names[-1] == f"04 Medical and dental costs/{LAST}-03-10 Dr. Muster - invoice.pdf"


async def test_a_zip_names_the_files_left_out(user: User, ctx, setup) -> None:
    doctor = spend(ctx, setup, 240, "06.2")
    att = att_svc.add(ctx, doctor.id, "invoice.pdf", PDF)
    att_svc.path(ctx, att).unlink()
    await user.open("/tax")
    await menu_item(user, "Proofs as ZIP…")
    await until(lambda: user.download.http_responses)
    assert notified(user, "1 file was left out: The file of “invoice.pdf” is missing from the data folder")


async def test_a_zip_too_large_is_refused(user: User, ctx, setup, monkeypatch) -> None:
    doctor = spend(ctx, setup, 240, "06.2")
    att_svc.add(ctx, doctor.id, "invoice.pdf", PDF)
    monkeypatch.setattr(tax_page.tax_export, "MAX_ZIP_BYTES", 10)
    await user.open("/tax")
    await menu_item(user, "Proofs as ZIP…")
    await until(lambda: notified(user, "The attachments are too large for one file."))
    assert not user.download.http_responses


async def test_in_the_app_window_a_proof_opens_in_the_system_viewer(user: User, ctx, setup, monkeypatch) -> None:
    doctor = spend(ctx, setup, 240, "06.2")
    att = att_svc.add(ctx, doctor.id, "invoice.pdf", PDF)
    opened = []
    monkeypatch.setattr(att_svc, "system_open", opened.append)
    monkeypatch.setattr(app.native, "main_window", Window())
    await user.open("/tax")
    click(user, newest(user, ui.button, text="invoice.pdf"))
    assert opened == [att_svc.path(ctx, att)]
    att_svc.path(ctx, att).write_bytes(b"MZ not a pdf")
    click(user, newest(user, ui.button, text="invoice.pdf"))
    assert user.notify.contains("isn’t opened")


async def test_a_dividend_shows_its_tax_withheld(user: User, ctx, setup) -> None:
    depot = acc_svc.add_account(ctx, "Depot", "CHF", asset_class=ASSET_SECURITIES)
    nestle = inv_svc.add_holding(ctx, depot.id, "Nestlé", "CH0038863350")
    inv_svc.save_trade(
        ctx,
        TradeInput(TRADE_DIVIDEND, nestle.id, date(LAST, 4, 28), amount=100, tax=35, cash_account=setup["chf"].id),
    )
    await user.open("/tax")
    shown = labels(user)
    assert "tax withheld 35.00" in shown and "Tax withheld" in shown
    assert shown.count("35.00 CHF") == 3  # the reclaimable section's total and row, the securities list's tax
    assert "Nestlé (CH0038863350)" in shown
