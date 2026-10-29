"""NiceGUI main file for the UI tests: the app's pages, on the context the test fixture prepared."""

from nicegui import app, ui

from budget_app import i18n
from budget_app.main import register_pages, register_routes
from budget_app.ui import theme

i18n.set_language("en")
theme.install()
register_routes(app)
register_pages()
ui.run(reload=False)
