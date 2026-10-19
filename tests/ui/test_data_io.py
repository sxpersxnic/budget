"""Exports of the list pages (F24) and the Data section in Settings: backup, restore, JSON (F25)."""

import asyncio
import io
import json
from datetime import datetime

import httpx
import pandas as pd
import pytest
from nicegui import ElementFilter, ui
from nicegui.element import Element
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from nicegui.testing.user_interaction import UserInteraction

from budget_app.services import archive
from budget_app.services import export as export_svc
from budget_app.storage import backup


# ------------------------------------------------------------------ helpers
async def settle() -> None:
    for _ in range(3):
        await asyncio.sleep(0.01)


async def click(user: User, element: Element) -> None:
    UserInteraction(user, {element}, None).click()
    await settle()


async def menu_item(user: User, text: str) -> None:
    """Menus are closed in the simulation, so their items count as hidden; click by text."""
    with user:
        items = [
            i
            for i in ElementFilter(kind=ui.menu_item, local_scope=False)
            if any(getattr(e, "text", None) == text for e in i.descendants())
        ]
    assert items, f"no menu item {text!r}"
    await click(user, items[0])


def button(user: User, text: str) -> ui.button:
    with user:
        found = [b for b in ElementFilter(kind=ui.button, local_scope=False) if b.text == text and b.visible]
    assert found, f"no button {text!r}"
    return found[-1]  # the newest, e.g. in the sheet that just opened


async def downloaded(user: User) -> httpx.Response:
    for _ in range(100):
        if user.download.http_responses:
            return user.download.http_responses[-1]
        await asyncio.sleep(0.05)
    raise AssertionError("no download happened")


async def upload(user: User, name: str, data: bytes) -> None:
    [element] = user.find(kind=ui.upload).elements
    await element.handle_uploads([SmallFileUpload(name, "application/zip", data)])
    await settle()


# ------------------------------------------------------------------ list exports (F24)
async def test_transactions_export_follows_the_filters(user: User, demo) -> None:
    await user.open("/transactions")
    with user:
        [typ] = [s for s in ElementFilter(kind=ui.select, local_scope=False) if s.props.get("label") == "Type"]
        typ.value = "income"
    await settle()
    await menu_item(user, "Export as CSV…")
    response = await downloaded(user)
    df = pd.read_csv(io.BytesIO(response.content))
    assert set(df["Type"]) == {"Income"} and len(df) == 6
    assert list(df.columns[:4]) == ["Date", "Category", "Amount", "Currency"]
    assert set(df["Currency"]) == {"CHF"}
    assert list(df["Date"]) == sorted(df["Date"], reverse=True)  # newest first, like the list


async def test_budget_plan_export_as_excel(user: User, ctx, demo) -> None:
    await user.open("/budget-entries")
    await menu_item(user, "Export as Excel…")
    response = await downloaded(user)
    assert response.content[:2] == b"PK"
    df = pd.read_excel(io.BytesIO(response.content))
    assert len(df) == len(ctx.store.budget.all())
    assert {"Month", "Category", "Amount", "Currency", "Repeats"} <= set(df.columns)


async def test_a_failed_list_export_is_reported(user: User, demo, monkeypatch) -> None:
    def broken(*_args, **_kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(export_svc, "view_frame", broken)
    await user.open("/transactions")
    await menu_item(user, "Export as Excel…")
    await user.should_see("Export failed: disk full")
    assert not user.download.http_responses


# ------------------------------------------------------------------ Settings · Data (F25)
async def test_back_up_now_hands_over_a_restorable_zip(user: User, ctx, demo) -> None:
    await user.open("/settings")
    await user.should_see("Data Folder")
    await user.should_see(str(ctx.store.data_dir))
    await click(user, button(user, "Back Up Now…"))
    response = await downloaded(user)
    files = archive.read(response.content)
    assert "transactions.csv" in files and "settings.json" in files


async def test_export_everything_as_json(user: User, ctx, demo) -> None:
    await user.open("/settings")
    await click(user, button(user, "Export as JSON…"))
    doc = json.loads((await downloaded(user)).content)
    assert doc["format"] == "budget-app/export"
    assert len(doc["transactions"]) == len(ctx.store.transactions.all())


async def test_restore_from_a_backup_file(user: User, ctx, demo) -> None:
    data = archive.create(ctx)
    count = len(ctx.store.transactions.all())
    ctx.store.transactions.delete([tx.id for tx in ctx.store.transactions.all()][:5])
    await user.open("/settings")
    await upload(user, "Budget-backup-2026-10-19.zip", data)
    await user.should_see("Replace all records with the backup “Budget-backup-2026-10-19.zip”?")
    await click(user, button(user, "Restore"))
    assert len(ctx.store.transactions.all()) == count
    await user.should_see("Restored. The previous records are kept in")
    assert [b.reason for b in archive.saved_backups(ctx)] == ["restore"]


async def test_cancelling_a_restore_changes_nothing(user: User, ctx, demo) -> None:
    data = archive.create(ctx)
    ctx.store.transactions.delete([tx.id for tx in ctx.store.transactions.all()][:5])
    left = len(ctx.store.transactions.all())
    await user.open("/settings")
    await upload(user, "backup.zip", data)
    await click(user, button(user, "Cancel"))
    assert len(ctx.store.transactions.all()) == left
    assert archive.saved_backups(ctx) == []


async def test_a_file_that_is_no_backup_is_refused(user: User, ctx, demo) -> None:
    await user.open("/settings")
    await upload(user, "photo.zip", b"not a zip at all")
    await user.should_see("This is not a Budget backup (not a ZIP file).")
    assert archive.saved_backups(ctx) == []


async def test_a_backup_failing_at_restore_time_is_reported(user: User, ctx, demo, monkeypatch) -> None:
    data = archive.create(ctx)
    await user.open("/settings")
    await upload(user, "backup.zip", data)

    def broken(*_args, **_kw):
        raise archive.ValidationError("The backup can’t be read: boom")

    monkeypatch.setattr(archive, "restore", broken)
    await click(user, button(user, "Restore"))
    await user.should_see("The backup can’t be read: boom")


async def test_automatic_backups_are_listed_and_restorable(user: User, ctx, demo) -> None:
    await user.open("/settings")
    await user.should_see("The app copies your records here before an update or a restore. None so far.")

    backup.backup_on_version_change(ctx.store.data_dir, "9.9.9", now=datetime(2026, 1, 2, 3, 4, 5))
    count = len(ctx.store.transactions.all())
    ctx.store.transactions.delete([tx.id for tx in ctx.store.transactions.all()])
    await user.open("/settings")
    await user.should_see("Before an update")
    await user.should_see("2 Jan 2026, 03:04")
    await click(user, button(user, "Restore…"))
    await user.should_see("Replace all records with the backup of 2 Jan 2026, 03:04?")
    await click(user, button(user, "Restore"))
    assert len(ctx.store.transactions.all()) == count


async def test_an_empty_automatic_backup_is_refused(user: User, ctx, demo) -> None:
    (ctx.store.data_dir / backup.BACKUP_DIR / "2026-01-02_030405_before-restore").mkdir(parents=True)
    await user.open("/settings")
    await user.should_see("Before a restore")
    await click(user, button(user, "Restore…"))
    await user.should_see("The backup contains no records.")


@pytest.mark.parametrize("lang", ["fr", "it"])
async def test_settings_offers_french_and_italian(user: User, ctx, lang: str) -> None:
    from budget_app import i18n  # noqa: PLC0415

    await user.open("/settings")
    with user:
        [sel] = [s for s in ElementFilter(kind=ui.select, local_scope=False) if lang in s.options]
        assert sel.options[lang] == i18n.LANGUAGES[lang]
        sel.value = lang
    await settle()
    assert ctx.store.settings.language == lang
    i18n.set_language("en")
