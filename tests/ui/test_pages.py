import pytest
from nicegui.testing import User

PAGES = {
    "/": "Overview",
    "/budget": "Budget",
    "/transactions": "Transactions",
    "/add-transaction": "New Transaction",
    "/import": "Import",
    "/budget-entries": "Budget Plan",
    "/add-budget": "New Budget Entry",
    "/categories": "Categories",
    "/settings": "Settings",
}


@pytest.mark.parametrize("path", PAGES)
async def test_every_page_opens_empty(user: User, path: str) -> None:
    await user.open(path)
    await user.should_see(PAGES[path])


@pytest.mark.parametrize("path", PAGES)
async def test_every_page_opens_with_data(user: User, demo, path: str) -> None:
    await user.open(path)
    await user.should_see(PAGES[path])
