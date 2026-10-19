"""NiceGUI main file for the UI tests: the app's pages, on the context the test fixture prepared."""

from nicegui import ui

from budget_app import i18n
from budget_app.main import PAGES
from budget_app.ui import theme

i18n.set_language("en")
theme.install()
for path, builder in PAGES.items():
    ui.page(path, title="Budget")(builder)
ui.run(reload=False)
