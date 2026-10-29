"""The lock page (F27): the password before anything else is shown, and the way back in with the recovery key."""

from __future__ import annotations

from nicegui import ui

from budget_app.i18n import t
from budget_app.services import profiles as profiles_svc
from budget_app.services import security
from budget_app.services.context import ValidationError
from budget_app.storage.vault import FILE_NAME
from budget_app.ui import theme
from budget_app.ui.layout import error, open_profile


def _next_path() -> str:
    request = ui.context.client.request
    target = request.query_params.get("next", "/") if request is not None else "/"
    return target if target.startswith("/") and "//" not in target else "/"


def page() -> None:
    theme.apply_colors()
    ui.page_title(f"{t('Locked')} – Budget")
    if not security.is_local(ui.context.client.ip):  # F35: other devices pair, they never get the password page
        ui.navigate.to("/pair")
        return
    if not security.required():  # nothing to unlock
        ui.navigate.to("/")
        return
    target = _next_path()
    busy = False

    async def unlock() -> None:
        nonlocal busy
        if busy:
            return
        busy = True
        button.set_enabled(False)
        try:
            nonce = await security.unlock(password.value or "", target)
        except ValidationError as exc:
            error(str(exc))
            password.value = ""
            return
        finally:
            busy = False
            button.set_enabled(True)
        ui.navigate.to(f"/unlock/{nonce}")

    async def recover() -> None:
        nonlocal busy
        if busy:
            return
        if new_password.value != repeat.value:
            error(t("The passwords differ"))
            return
        busy = True
        try:
            nonce = await security.recover(recovery_key.value or "", new_password.value or "", target)
        except ValidationError as exc:
            error(str(exc))
            return
        finally:
            busy = False
        ui.navigate.to(f"/unlock/{nonce}")

    def toggle_help() -> None:
        help_box.set_visibility(not help_box.visible)

    with ui.column().classes("lock-page items-center"), ui.column().classes("lock-card items-stretch gap-3"):
        with ui.row().classes("items-center gap-2 self-center pb-1"):
            ui.icon("lock", size="26px").classes("text-primary")
            ui.label("Budget").classes("text-[20px] font-semibold")
        reg = profiles_svc.load()
        if len(reg.profiles) > 1:  # F34: which profile is locked
            ui.label(reg.active.name).classes("text-[15px] font-semibold self-center")
        ui.label(t("Enter your password to open your records.")).classes("footnote text-center")
        password = (
            ui.input(t("Password"), password=True, password_toggle_button=True)
            .props("dense outlined autofocus")
            .classes("w-full")
        )
        password.on("keydown.enter", unlock)
        button = ui.button(t("Unlock"), on_click=unlock).props("unelevated no-caps").classes("w-full")
        ui.button(t("Forgot your password?"), on_click=toggle_help).props("flat dense no-caps").classes(
            "link-btn self-center"
        )
        with ui.column().classes("w-full gap-3 pt-2") as help_box:
            if security.encrypted():
                ui.label(
                    t("Type the recovery key you were given when encryption was turned on, and choose a new password.")
                ).classes("footnote")
                recovery_key = ui.input(t("Recovery key")).props("dense outlined").classes("w-full mono")
                new_password = (
                    ui.input(t("New password"), password=True, password_toggle_button=True)
                    .props("dense outlined")
                    .classes("w-full")
                )
                repeat = ui.input(t("Repeat the new password"), password=True).props("dense outlined").classes("w-full")
                repeat.on("keydown.enter", recover)
                ui.button(t("Set New Password"), on_click=recover).props("unelevated no-caps").classes("w-full")
            else:
                ui.label(
                    t(
                        "Your records are not encrypted, so nothing is lost. To remove the lock, quit the app and "
                        "delete the file {file} from the data folder; see the README.",
                        file=FILE_NAME,
                    )
                ).classes("footnote")
        help_box.set_visibility(False)
        _other_profiles()


def _other_profiles() -> None:
    """The way to another profile (F34) without this one's password: each profile has its own lock."""
    reg = profiles_svc.load()
    others = [p for p in reg.profiles if p.id != reg.current]
    if not others:
        return
    with (
        ui.button(t("Open Another Profile"), icon="o_folder_shared")
        .props("flat dense no-caps")
        .classes("link-btn self-center"),
        ui.menu().props("auto-close"),
    ):
        for p in others:
            ui.menu_item(p.name, on_click=lambda p=p: open_profile(p.id))
