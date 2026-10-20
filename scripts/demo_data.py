"""Fill a data folder with a year of example records for manual testing: `just demo`.

Usage: `uv run python scripts/demo_data.py demo-data`

The folder is emptied first, so every run starts from the same data (dated relative to today).
Records go through the services, as if entered in the app, so they pass the same validation.
"""

from __future__ import annotations

import random
import shutil
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from budget_app import config
from budget_app.domain import months
from budget_app.domain.fx import FxService, download_ecb_csv
from budget_app.domain.models import (
    REVAL_INVEST,
    TX_EXCHANGE,
    TX_EXPENDITURE,
    TX_INCOME,
    TX_REVALUATION,
    TX_TRANSFER,
)
from budget_app.services import accounts as acc_svc
from budget_app.services import budget as budget_svc
from budget_app.services import items as items_svc
from budget_app.services import templates as tpl_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.context import Context
from budget_app.services.items import ItemInput
from budget_app.services.transactions import TransactionInput
from budget_app.storage import backup
from budget_app.storage import lock as instance_lock
from budget_app.storage.store import Store

MONTHS = 12  # months of history before the current one
GROCERIES = [("Bread", 3.2), ("Milk", 1.6), ("Cheese", 7.9), ("Apples", 4.5), ("Coffee", 9.5), ("Pasta", 2.3)]


def reset(folder: Path) -> None:
    """Empty `folder`, refusing while the app runs on it (the lock stays held until this script exits)."""
    folder.mkdir(parents=True, exist_ok=True)
    instance_lock.acquire(folder)
    for entry in folder.iterdir():
        if entry.name == ".lock":
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    backup.backup_on_version_change(folder, config.app_version())  # marks the folder; nothing to back up yet


def install_rates(folder: Path) -> None:
    """Copy the dev folder's ECB file (works offline), or download it."""
    target = folder / "ecb" / config.ECB_CSV_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    local = config.PROJECT_ROOT / "data" / "ecb" / config.ECB_CSV_NAME
    if local.is_file():
        shutil.copy2(local, target)
    else:
        download_ecb_csv(target)


def seed(ctx: Context) -> None:
    rng = random.Random(42)  # noqa: S311 - not for security; same amounts on every run
    today = date.today()
    cm = months.current_month()
    first = months.add_months(cm, -MONTHS)

    bank = acc_svc.add_account(ctx, "Bank", "CHF")
    savings = acc_svc.add_account(ctx, "Savings", "CHF")
    euro = acc_svc.add_account(ctx, "Euro account", "EUR")
    salary = acc_svc.add_income_category(ctx, "Salary")
    side_job = acc_svc.add_income_category(ctx, "Side job")

    # Month-end balances follow the bookings, so most months reconcile exactly
    balance = {bank.id: 5000.0, savings.id: 20000.0, euro.id: 300.0}
    flows: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    def book(inp: TransactionInput, items: list[ItemInput] | None = None) -> None:
        if inp.date is None or inp.date > today:
            return
        tx = tx_svc.save(ctx, inp)
        if items:
            items_svc.replace(ctx, tx.id, items)
        m = inp.date.strftime("%Y-%m")
        amount = float(inp.amount or inp.reval_change or 0)
        if inp.type == TX_INCOME:
            flows[m][inp.to_account] += amount
        elif inp.type == TX_EXPENDITURE:
            flows[m][inp.from_account] -= amount
        elif inp.type in (TX_TRANSFER, TX_EXCHANGE):
            flows[m][inp.from_account] -= amount
            flows[m][inp.to_account] += float(inp.to_amount or amount)
        elif inp.type == TX_REVALUATION:
            flows[m][inp.reval_account] += amount

    def spend(
        d: date, amount: float, code: str, account: str = bank.id, currency: str = "CHF", comment: str = ""
    ) -> None:
        book(
            TransactionInput(
                date=d,
                type=TX_EXPENDITURE,
                amount=round(amount, 2),
                currency=currency,
                category=code,
                from_account=account,
                comment=comment,
            )
        )

    for k in range(MONTHS, -1, -1):
        m = months.add_months(cm, -k)
        y, mo = map(int, m.split("-"))

        def day(n: int, y: int = y, mo: int = mo) -> date:
            return date(y, mo, n)

        spend(day(1), 1900, "04.1", comment="Rent")
        spend(day(2), 85, "07.3", comment="Monthly pass")
        spend(day(15), 49.90, "08.3", comment="Mobile")
        spend(day(12), rng.uniform(40, 90), "09.4", account=euro.id, currency="EUR", comment="Streaming and cinema")
        for n in (3, 10, 17, 24):
            if n == 3:  # one receipt a month with its items
                basket = [ItemInput(name, rng.randint(1, 3), price) for name, price in rng.sample(GROCERIES, 4)]
                book(
                    TransactionInput(
                        date=day(n),
                        type=TX_EXPENDITURE,
                        amount=items_svc.total(basket),
                        currency="CHF",
                        category="01.1",
                        from_account=bank.id,
                        comment="Migros",
                    ),
                    basket,
                )
            else:
                spend(day(n), rng.uniform(70, 160), "01.1", comment="Coop")
        for n in rng.sample(range(5, 28), 2):
            spend(day(n), rng.uniform(25, 90), "11.1")
        if mo in (3, 9):
            spend(day(8), rng.uniform(120, 260), "06.2", comment="Doctor")
        if mo == 5:
            spend(day(20), 380, "07.2", comment="Car service")

        book(
            TransactionInput(
                date=day(25), type=TX_INCOME, amount=6200, currency="CHF", category=salary.id, to_account=bank.id
            )
        )
        if mo % 3 == 0:
            book(
                TransactionInput(
                    date=day(18),
                    type=TX_INCOME,
                    amount=400,
                    currency="CHF",
                    category=side_job.id,
                    to_account=bank.id,
                    comment="Tutoring",
                )
            )
        book(
            TransactionInput(
                date=day(26),
                type=TX_TRANSFER,
                amount=800,
                currency="CHF",
                from_account=bank.id,
                to_account=savings.id,
                comment="Savings",
            )
        )
        if mo % 4 == 0:
            received = round(ctx.convert_on(300, "CHF", "EUR", day(6)) * 0.98, 2)  # the bank's spread
            book(
                TransactionInput(
                    date=day(6),
                    type=TX_EXCHANGE,
                    amount=300,
                    currency="CHF",
                    from_account=bank.id,
                    to_account=euro.id,
                    to_amount=received,
                )
            )
        if mo == 12:
            book(
                TransactionInput(
                    date=day(31),
                    type=TX_REVALUATION,
                    currency="CHF",
                    category=REVAL_INVEST,
                    reval_account=savings.id,
                    reval_mode="difference",
                    reval_change=210,
                    comment="Fund value at year end",
                )
            )

        for acc_id, change in flows[m].items():
            balance[acc_id] += change
        if k > 0:  # the current month's balance is not known yet
            for acc_id, amount in balance.items():
                wealth_svc.upsert_entry(ctx, acc_id, m, round(amount, 2))

    # An advance for friends, half repaid: shows up as an open advance
    dinner = today.replace(day=1) if today.day > 1 else today
    adv = tx_svc.save(
        ctx,
        TransactionInput(
            date=dinner,
            type=TX_EXPENDITURE,
            amount=240,
            currency="CHF",
            category="11.1",
            from_account=bank.id,
            is_advance=True,
            advance_mode="amount",
            advance_value=160,
            debtor="Sam",
            comment="Birthday dinner",
        ),
    )
    tx_svc.save(
        ctx,
        TransactionInput(
            date=today,
            type=TX_INCOME,
            amount=80,
            currency="CHF",
            to_account=bank.id,
            is_advance=True,
            advance_account=adv.advance_account,
            settles_id=adv.id,
            comment="Sam, first half",
        ),
    )

    for type_, code, amount, currency in (
        ("expenditure", "04.1", 1900, "CHF"),
        ("expenditure", "01.1", 550, "CHF"),
        ("expenditure", "07.3", 85, "CHF"),
        ("expenditure", "08.3", 50, "CHF"),
        ("expenditure", "11.1", 120, "CHF"),
        ("expenditure", "09.4", 60, "EUR"),
        ("income", salary.id, 6200, "CHF"),
    ):
        budget_svc.create(
            ctx,
            BudgetInput(start_month=first, forever=True, type=type_, currency=currency, amount=amount, category=code),
        )

    tpl_svc.save(
        ctx,
        "Rent",
        TransactionInput(
            type=TX_EXPENDITURE, amount=1900, currency="CHF", category="04.1", from_account=bank.id, comment="Rent"
        ),
    )
    tpl_svc.save(
        ctx,
        "Monthly pass",
        TransactionInput(type=TX_EXPENDITURE, amount=85, currency="CHF", category="07.3", from_account=bank.id),
    )
    tpl_svc.save(
        ctx,
        "To savings",
        TransactionInput(type=TX_TRANSFER, amount=800, currency="CHF", from_account=bank.id, to_account=savings.id),
    )


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: demo_data.py FOLDER")
    folder = Path(sys.argv[1]).resolve()
    try:
        reset(folder)
    except instance_lock.AlreadyRunningError as exc:
        sys.exit(f"{exc}; close it first.")
    install_rates(folder)
    ctx = Context(store=Store(folder), fx_service=FxService(folder))
    seed(ctx)
    (folder / "history.jsonl").unlink(missing_ok=True)  # start with an empty change history
    print(f"Demo data written to {folder}")


if __name__ == "__main__":
    main()
