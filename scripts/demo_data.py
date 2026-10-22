"""Fill a data folder with a year of example records for manual testing: `just demo`.

Usage: `uv run python scripts/demo_data.py demo-data`

The folder is emptied first, so every run starts from the same data (dated relative to today).
Records go through the services, as if entered in the app, so they pass the same validation.
"""

from __future__ import annotations

import io
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
    ASSET_CRYPTO,
    ASSET_PILLAR3A,
    ASSET_SECURITIES,
    GOAL_MONTHS,
    REVAL_INVEST,
    TRADE_BUY,
    TRADE_DIVIDEND,
    TRADE_SELL,
    TX_EXCHANGE,
    TX_EXPENDITURE,
    TX_INCOME,
    TX_REVALUATION,
    TX_TRANSFER,
)
from budget_app.services import accounts as acc_svc
from budget_app.services import attachments as att_svc
from budget_app.services import budget as budget_svc
from budget_app.services import goals as goal_svc
from budget_app.services import investments as inv_svc
from budget_app.services import items as items_svc
from budget_app.services import tags as tag_svc
from budget_app.services import tax as tax_svc
from budget_app.services import templates as tpl_svc
from budget_app.services import transactions as tx_svc
from budget_app.services import wealth as wealth_svc
from budget_app.services.budget import BudgetInput
from budget_app.services.context import Context
from budget_app.services.goals import GoalInput
from budget_app.services.investments import TradeInput
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


def receipt(title: str, lines: list[str]) -> bytes:
    """A one-page PDF receipt, to show attachments as proofs in the tax summary (F20)."""
    from reportlab.lib.pagesizes import A5  # noqa: PLC0415 - only the demo needs it
    from reportlab.pdfgen import canvas  # noqa: PLC0415

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A5)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(40, 540, title)
    pdf.setFont("Helvetica", 10)
    for i, line in enumerate(lines):
        pdf.drawString(40, 510 - 16 * i, line)
    pdf.save()
    return buf.getvalue()


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
    interest = acc_svc.add_income_category(ctx, "Interest")  # F20: counts for the securities list
    refunds = acc_svc.add_income_category(ctx, "Refunds")

    # Month-end balances follow the bookings, so most months reconcile exactly
    # F21: a securities account and a crypto wallet valued from their holdings, and a pillar 3a account
    depot = acc_svc.add_account(ctx, "Depot", "CHF", asset_class=ASSET_SECURITIES)
    wallet = acc_svc.add_account(ctx, "Crypto wallet", "CHF", asset_class=ASSET_CRYPTO)
    pillar = acc_svc.add_account(ctx, "Pillar 3a", "CHF", asset_class=ASSET_PILLAR3A)
    etf = inv_svc.add_holding(ctx, depot.id, "SPI ETF", "CH0237935652")
    nestle = inv_svc.add_holding(ctx, depot.id, "Nestlé", "CH0038863350")
    btc = inv_svc.add_holding(ctx, wallet.id, "Bitcoin", "BTC")
    quote = {etf.id: 150.0, nestle.id: 98.0, btc.id: 52000.0}
    moves = {etf.id: (-0.03, 0.04), nestle.id: (-0.04, 0.03), btc.id: (-0.12, 0.15)}

    # F10: a holiday with a budget for its period, a renovation with an open-ended one, and a plain tag
    holiday = tag_svc.add(ctx, "Holiday Italy", "green")
    renovation = tag_svc.add(ctx, "Renovation", "orange")
    business = tag_svc.add(ctx, "Business", "blue")
    # F20: tags linked to sections of the tax summary
    work = tag_svc.add(ctx, "Work", "teal")
    loan = tag_svc.add(ctx, "Loan interest", "purple")
    refund = tag_svc.add(ctx, "Health refund", "red")
    tax_svc.set_links(ctx, {"professional": [work.id], "debt_interest": [loan.id], "medical": [refund.id]})

    balance = {bank.id: 5000.0, savings.id: 20000.0, euro.id: 300.0, pillar.id: 18000.0}
    flows: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    def book(
        inp: TransactionInput, items: list[ItemInput] | None = None, proof: tuple[str, bytes] | None = None
    ) -> None:
        if inp.date is None or inp.date > today:
            return
        tx = tx_svc.save(ctx, inp)
        if proof:
            att_svc.add(ctx, tx.id, *proof)
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
        d: date,
        amount: float,
        code: str,
        account: str = bank.id,
        currency: str = "CHF",
        comment: str = "",
        payee: str = "",
        tags: tuple[str, ...] = (),
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
                payee=payee,
                tags=list(tags),
            )
        )

    for k in range(MONTHS, -1, -1):
        m = months.add_months(cm, -k)
        y, mo = map(int, m.split("-"))

        def day(n: int, y: int = y, mo: int = mo) -> date:
            return date(y, mo, n)

        spend(day(1), 1900, "04.1", comment="Rent", payee="Immo Muster AG")
        spend(day(2), 85, "07.3", comment="Monthly pass", payee="ZVV")
        spend(day(15), 49.90, "08.3", comment="Mobile", payee="Swisscom")
        spend(
            day(12),
            rng.uniform(40, 90),
            "09.4",
            account=euro.id,
            currency="EUR",
            comment="Streaming and cinema",
            payee=rng.choice(["Netflix", "Kino Arthouse"]),
        )
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
                        payee="Migros",
                    ),
                    basket,
                )
            else:
                spend(day(n), rng.uniform(70, 160), "01.1", comment="Coop", payee="Coop")
        for n in rng.sample(range(5, 28), 2):
            spend(day(n), rng.uniform(25, 90), "11.1", payee=rng.choice(["Restaurant Hirschen", "tibits", "Holy Cow"]))
        if mo in (3, 9):
            spend(day(8), rng.uniform(120, 260), "06.2", comment="Doctor")
        # F20: deductible items for the tax summary, with receipts as proofs
        spend(day(1), 412.60, "12.1.2", comment="Health insurance premium", payee="CSS")
        if mo in (1, 4, 7, 10):
            spend(
                day(30),
                96.50,
                "12.2.9.9",
                comment="Car loan interest",
                payee="Cembra",
                tags=(loan.id,),
            )
        if mo in (3, 11):
            doctor = round(rng.uniform(180, 320), 2)
            book(
                TransactionInput(
                    date=day(16),
                    type=TX_EXPENDITURE,
                    amount=doctor,
                    currency="CHF",
                    category="06.2.2",
                    from_account=bank.id,
                    comment="Dental check-up",
                    payee="Zahnarztpraxis Muster",
                ),
                proof=(
                    "Invoice dentist.pdf",
                    receipt("Zahnarztpraxis Muster", ["Dental check-up", f"CHF {doctor:.2f}"]),
                ),
            )
        if mo == 12:
            book(
                TransactionInput(
                    date=day(5),
                    type=TX_EXPENDITURE,
                    amount=150,
                    currency="CHF",
                    category="14.5",
                    from_account=bank.id,
                    comment="Winter appeal",
                    payee="Glückskette",
                ),
                proof=("Donation receipt.pdf", receipt("Glückskette", ["Thank you for your donation", "CHF 150.00"])),
            )
            book(
                TransactionInput(
                    date=day(31),
                    type=TX_INCOME,
                    amount=23.40,
                    currency="CHF",
                    category=interest.id,
                    to_account=savings.id,
                    comment="Interest on savings",
                    payee="Bank",
                )
            )
        if k == 2:
            spend(day(4), 640, "10.5", comment="Excel course", payee="Klubschule Migros")
            spend(day(19), 129, "13.2.9.1", comment="Laptop bag for work", payee="Digitec", tags=(work.id,))
        if mo == 11:
            book(
                TransactionInput(
                    date=day(28),
                    type=TX_INCOME,
                    amount=120,
                    currency="CHF",
                    category=refunds.id,
                    to_account=bank.id,
                    comment="Refund of the dentist bill",
                    payee="CSS",
                    tags=[refund.id],
                )
            )
        if mo == 5:
            spend(day(20), 380, "07.2", comment="Car service")
        if k == 3:  # a week in Italy
            trip = (holiday.id,)
            spend(day(6), 186.40, "07.3", comment="Train to Florence", payee="SBB", tags=trip)
            spend(day(6), 1240, "11.2", comment="Hotel, 6 nights", payee="Hotel Bellavista", tags=trip)
            spend(day(8), 64.50, "11.1", account=euro.id, currency="EUR", payee="Trattoria da Mario", tags=trip)
            spend(day(9), 38, "09.4", account=euro.id, currency="EUR", comment="Uffizi", tags=trip)
            spend(day(10), 92.80, "11.1", account=euro.id, currency="EUR", payee="Osteria Nove", tags=trip)
            spend(day(12), 186.40, "07.3", comment="Train home", payee="SBB", tags=trip)
        if k == 1:  # F11: one supermarket receipt split by its lines; the gift carries a tag of its own
            gift = tag_svc.add(ctx, "Gifts", "pink")
            book(
                TransactionInput(
                    date=day(14),
                    type=TX_EXPENDITURE,
                    amount=96.85,
                    currency="CHF",
                    category="01.1",
                    from_account=bank.id,
                    comment="Migros, with presents",
                    payee="Migros",
                ),
                [
                    ItemInput("Bread", 1, 3.20),
                    ItemInput("Cheese", 2, 6.90),
                    ItemInput("Apples", 1, 4.50),
                    ItemInput("Detergent", 1, 12.95, "05.6.1.1"),
                    ItemInput("Toilet paper", 1, 8.40, "05.6.1.1"),
                    ItemInput("Board game", 1, 34.90, "09.2.1", [gift.id]),
                    ItemInput("Birthday card", 1, 4.90, "09.2.1", [gift.id]),
                ],
            )
        if k == 0:  # F18: something notable this month, a large payment in a category that is usually empty
            spend(day(min(today.day, 8)), 1290, "05.3.1.2", comment="Washing machine", payee="Fust")
        if k in (2, 1):  # the bathroom
            redo = (renovation.id,)
            spend(day(7), rng.uniform(900, 1300), "04.3.1", comment="Tools and tiles", payee="Jumbo", tags=redo)
            spend(day(21), rng.uniform(600, 900), "04.3.2", comment="Plumber", payee="Sanitär Huber", tags=redo)

        book(
            TransactionInput(
                date=day(25),
                type=TX_INCOME,
                amount=6200,
                currency="CHF",
                category=salary.id,
                to_account=bank.id,
                payee="Muster AG",
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
                    tags=[business.id],
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

        book(
            TransactionInput(
                date=day(27),
                type=TX_TRANSFER,
                amount=588,
                currency="CHF",
                from_account=bank.id,
                to_account=pillar.id,
                comment="Pillar 3a",
            )
        )

        def trade(
            kind: str,
            holding: str,
            d: date,
            quantity: float = 0.0,
            fee: float = 0.0,
            amount: float = 0.0,
            tax: float = 0.0,
        ) -> None:
            if d > today:
                return
            inv_svc.save_trade(
                ctx,
                TradeInput(kind, holding, d, quantity, quote[holding], fee, amount, tax, cash_account=bank.id),
            )
            month = d.strftime("%Y-%m")
            money = round(quantity * quote[holding], 2)
            flows[month][bank.id] += {TRADE_BUY: -money, TRADE_SELL: money}.get(kind, amount - tax) - fee

        trade(TRADE_BUY, etf.id, day(5), round(500 / quote[etf.id], 6), fee=1.5)  # a monthly savings plan
        if k == 10:
            trade(TRADE_BUY, nestle.id, day(12), 20, fee=9.9)
        if k == 8:
            trade(TRADE_BUY, btc.id, day(9), 0.02, fee=2)
        if k == 4:
            trade(TRADE_SELL, nestle.id, day(14), 8, fee=9.9)
        if mo == 4 and k < 10:
            held = inv_svc.performance(ctx, nestle.id).quantity
            gross = round(held * 3.05, 2)  # 35 % Swiss withholding tax: the bank receives the net
            trade(TRADE_DIVIDEND, nestle.id, day(28), amount=gross, tax=round(gross * 0.35, 2))
        for holding, (low, high) in moves.items():
            quote[holding] = round(quote[holding] * (1 + rng.uniform(low, high)), 2)
            if months.last_day(m) <= today and inv_svc.steps(ctx, holding):
                inv_svc.save_price(ctx, holding, months.last_day(m), quote[holding])

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

    trip_start = date.fromisoformat(months.add_months(cm, -3) + "-01")
    tag_svc.set_budget(ctx, holiday.id, 2500, "CHF", trip_start, months.last_day(months.add_months(cm, -3)))
    tag_svc.set_budget(ctx, renovation.id, 3000, "CHF")

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

    # F14: a holiday saved for by hand and reserved in the projection, and an emergency fund on the savings account
    goal_svc.add(
        ctx,
        GoalInput(
            name="Holiday Japan",
            currency="CHF",
            target=6000,
            target_date=months.last_day(months.add_months(cm, 8)),
            saved=1500,
            reserve=True,
        ),
    )
    goal_svc.add(
        ctx, GoalInput(name="Emergency fund", currency="CHF", kind=GOAL_MONTHS, months=6, account_id=savings.id)
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
