# Feature catalog

Candidate features for the Budget app. Pick by ID; each entry says what the feature does, how
it would fit this app, roughly how big it is, and what has to be decided first.

The app's ground rules still apply to every feature: local first (data in CSV files on the
user's computer), CHF as the base currency with ECB rates, COICOP 2018 for expenditure, and
nothing uploaded without the user asking for it.

**Effort:** S = up to a day · M = a few days · L = one to two weeks · XL = more than two weeks.
**Network:** *Local* runs fully offline · *Optional* works offline, with an online extra the
user turns on · *Online* needs a service.

## Overview

| ID | Feature | Area | Effort | Network | Depends on |
| --- | --- | --- | --- | --- | --- |
| F01 | CSV import of transactions | Data in | M | Local | – |
| F02 | Swiss bank statement import (camt.053 / camt.054) | Data in | M | Local | F01 (preview, duplicates) |
| F03 | Swiss QR-bill scanning | Data in | M | Local | – |
| F04 | Receipt scanning | Data in | L | Optional | F12 |
| F05 | Recurring transactions | Data in | M | Local | – |
| F06 | Faster manual entry | Data in | S–M | Local | – |
| F07 | Automatic categorization | Organizing | M–L | Optional | F09 |
| F08 | Simpler category picker | Organizing | S | Local | – |
| F09 | Payees | Organizing | M | Local | – |
| F10 | Tags | Organizing | M | Local | – |
| F11 | Split transactions | Organizing | M | Local | – |
| F12 | Attachments | Organizing | M | Local | – |
| F13 | Bulk edit | Organizing | S | Local | – |
| F14 | Savings goals | Planning | M | Local | – |
| F15 | What-if scenarios | Planning | L | Local | – |
| F16 | Budget from history | Planning | S | Local | – |
| F17 | Subscription and recurring-spend detection | Insight | M | Local | F09 |
| F18 | Trends and unusual spending | Insight | M | Local | – |
| F19 | Year in review | Insight | M | Local | – |
| F20 | Swiss tax summary | Insight | M | Local | F10 |
| F21 | Investments and asset classes | Wealth | L | Optional | – |
| F22 | Loans and mortgages | Wealth | M | Local | – |
| F23 | Who owes what (shared expenses) | Wealth | M | Local | – |
| F24 | Export to CSV and JSON | Data out | S–M | Local | – |
| F25 | Full backup and restore | Data out | S | Local | – |
| F26 | Sync through a cloud folder | Data out | L | Optional | F28 |
| F27 | Encryption and app lock | Security | L | Local | – |
| F28 | Undo and change history | Security | M | Local | – |
| F29 | Multi-language support | Experience | L | Local | – |
| F30 | Onboarding and demo data | Experience | M | Local | – |
| F31 | Reminders and alerts | Experience | M | Local | – |
| F32 | Keyboard shortcuts and accessibility | Experience | M | Local | – |
| F33 | In-app update check | Experience | S | Optional | – |
| F34 | Several households or profiles | Experience | M | Local | – |
| F35 | Phone companion | Experience | XL | Optional | F27 |
| F36 | Closing balance from bank files | Data in | M | Local | F01 |
| F37 | COICOP information page | Organizing | S | Local | – (F40 adds notes) |
| F38 | Exchange-rate chart | Insight | S | Local | – |
| F39 | COICOP level 5 | Organizing | M | Local | source data |
| F40 | COICOP notes in search and on hover | Organizing | M | Local | source data |
| F41 | Opening balance and change per account | Wealth | S | Local | – |
| F42 | Reconciliation line instead of *Unaccounted* | Insight | S | Local | – |
| F43 | Revaluation transactions | Wealth | M | Local | – |
| F44 | Exchange spread booked as revaluation | Wealth | S–M | Local | B03, B06, F43 |
| F45 | Revaluations in the budget overview | Insight | S | Local | F43 |
| F46 | Unexplained difference per account | Wealth | S–M | Local | F41, F43 |
| F47 | Projection uses the higher of actual and budget | Planning | S–M | Local | – |

F36–F47 and the bugs B01–B09 come from the product owner's GitHub issue, called *issue 01*
here (local copy `docs/issues.local/ISSUE_01.md`, not committed); see [From issue 01](#from-issue-01) and
[Known bugs](#known-bugs-issue-01).

**Status:** F01 is built (branch `feat/import-export`). F29 is built for English and German;
French and Italian are open. Item lists on transactions (name, quantity, unit price) are built
(branch `feat/transaction-items`); they cover part of F11 and give F04 a place for receipt
lines. Issue 01 now comes before F24; see [Implementation order](#implementation-order).

## Implementation order

Issue 01 comes first. Steps run in the order given; *Needs* lists what has to be done first.

### Milestone 1 · Bugs from issue 01

| Step | Task | Needs | Effort |
| --- | --- | --- | --- |
| 1 | **B01, B02 · Downloads in the app window.** One save helper for every export: the system save dialog in the app window, a normal download in the browser. Fixes the statement export and the chart image, CSV and Excel exports; adds the missing chart PDF. | – | S |
| 2 | **B05 · Adding a chart.** Reproduce first; then the new chart scrolls into view and errors are shown instead of only logged. | – | S |
| 3 | **B07, B08 · Budget page on resize.** Account rows that reflow; income and expenditure side by side on wide windows, below each other on narrow ones. | decision 1 | S |
| 4 | **B03 · Exchange rate of the transaction date**, with a one-time recalculation of stored transactions. Comes before any step that adds up transactions per account. | – | S–M |
| 5 | **B06 · Received amount typed in** for currency exchanges. | – | S |
| 6 | **B09 · Until, repeat and note** in the budget edit dialog. | – | M |

### Milestone 2 · Accounts and reconciliation

| Step | Task | Needs | Effort |
| --- | --- | --- | --- |
| 7 | **F41 · Opening balance and change per account.** | – | S |
| 8 | **F42 · Reconciliation line** above the income and expenditure tables; closes B04. | decision 2 | S |
| 9 | **F47 · Projection uses the higher of actual and budget** per category. | decision 5 | S–M |

### Milestone 3 · Revaluations

| Step | Task | Needs | Effort |
| --- | --- | --- | --- |
| 10 | **F43 · Revaluation transactions.** | decision 3 | M |
| 11 | **F44 · Exchange spread booked as revaluation.** | B03, B06, F43 | S–M |
| 12 | **F45 · Revaluations section** in the budget overview; the reconciliation line from F42 then counts them. | F42, F43, decision 4 | S |
| 13 | **F46 · Unexplained difference per account**, with a *Book as revaluation* action. | F41, F43 | S–M |

### Milestone 4 · Import and charts

| Step | Task | Needs | Effort |
| --- | --- | --- | --- |
| 14 | **F36 · Closing balance from bank files.** | decision 6 | M |
| 15 | **F38 · Exchange-rate chart.** | – | S |
| 16 | **F37 · COICOP information page** with codes, names and search. | – | S |

### Milestone 5 · COICOP data

Waits for the source data (decision 7).

| Step | Task | Needs | Effort |
| --- | --- | --- | --- |
| 17 | **F40 · COICOP notes in search and on hover**; also shown on the F37 page. | decision 7 | M |
| 18 | **F39 · COICOP level 5.** | decision 7 | M |

### After issue 01

| Step | Task | Why here |
| --- | --- | --- |
| 19 | **F24** Export to CSV and JSON | Was next before the issue; reuses the save helper from step 1. |
| 20 | **F25** Full backup and restore, **F28** Undo and change history | Safety before more import features. |
| 21 | **F08** Simpler category picker, **F06** Faster manual entry, **F05** Recurring transactions | F08 builds on the COICOP select from F40; F05 shares the schedule code from B09. |
| 22 | **F09** Payees → **F07** Automatic categorization → **F13** Bulk edit | The *Stop typing* bundle: imports (F01, F36) become quicker to categorize. |
| 23 | **F02** camt.053 import | Reuses F01's preview and F36's closing balance; camt files carry the balance. |
| 24 | **F21** Investments and asset classes | Continues "capital gains on investment" from F43. |
| 25 | The rest, roughly by value per effort: F11, F12, F03, F10, F20, F16, F18, F14, F19, F29 (French, Italian), F30, F32, F31, F22, F23, F33, F34, F15, F17, F27, F26, F04, F35 | – |

The decisions are numbered as in [Decisions for the product owner](#decisions-for-the-product-owner).

---

## Getting data in

### F01 · CSV import of transactions

Today every transaction is typed by hand. Import a bank or card export instead.

- **Flow:** choose a file → map its columns (date, amount, sign or debit/credit, text,
  currency) → choose the account → preview with categories filled in where known → import.
- **Bank profiles:** the column mapping is saved per bank, so the next import is two clicks.
  CSV dialects vary (`;` vs `,`, `1'234.50` vs `1.234,50`, `dd.mm.yyyy`); the profile
  stores those too.
- **Duplicates:** each imported row gets a fingerprint (date, amount, account, text). Rows
  already imported are shown and skipped by default.
- **Fits:** imported rows become normal transactions, with the CHF amount at the ECB rate of
  the booking date. Since issue 01 (B03) manual entries use the rate of their date too.
- **Open since issue 01:** the closing balance in the file is not used yet (F36); categories
  are set for selected rows only, not per cell; transfers between own accounts can't be
  marked as such and must be given a category.
- **Decide:** which banks to support first; whether imports may create accounts; how to treat
  transfers between own accounts that appear in two files.

### F02 · Swiss bank statement import (camt.053 / camt.054)

Swiss banks deliver statements as ISO 20022 XML (camt.053 for account statements, camt.054
for notifications); since the SIX migration this is the standard e-banking export.

- Needs no column mapping: amounts, currency, booking date, counterparty, reference and the
  bank's own transaction ID are all structured, so duplicate detection is exact.
- Reuses F01's preview and duplicate handling.
- **Decide:** whether to also read the older MT940 format some banks still offer.

### F03 · Swiss QR-bill scanning

Every Swiss invoice carries a QR code with the amount, currency, creditor and reference.

- Drop a PDF or photo of the bill (or several) → the QR code is decoded locally → a
  pre-filled expenditure opens with amount, currency, creditor as payee and the reference
  as the note.
- Optionally keep the bill as an attachment (F12) and mark it *unpaid* until paid.
- Decoding is exact (no OCR guesswork) and needs only a small QR library.
- **Decide:** whether the app should track open bills (due date, paid/unpaid) or only record
  the payment.

### F04 · Receipt scanning

Photograph or drop a till receipt; the app reads merchant, date, total and, ideally, the
lines.

- **Local option:** OCR on the computer (Apple Vision on macOS, Tesseract elsewhere). Private
  and free, but reading totals from crumpled receipts is unreliable; expect the user to check.
- **Online option:** a vision-capable AI model reads the receipt far more reliably, including
  lines and VAT, but the image leaves the computer. Must be opt-in, per receipt or as a
  setting, with a clear statement of where the image goes.
- The result opens as a pre-filled transaction; with F11, the lines can become a split.
- The image is stored as an attachment (F12).
- **Decide:** local only, online only, or local with an online "read better" button; whether
  line items matter or the total is enough.

### F05 · Recurring transactions

Rent, insurance premiums and subscriptions repeat every month or year.

- Define a series (amount, accounts, category, rhythm, start and end) once.
- **Modes:** create the transactions automatically on their date, or list them as *due* for
  one-click confirmation (safer when amounts vary).
- The budget projection can use them directly, instead of a separate budget entry.
- **Decide:** auto-create versus confirm; how edits to a series affect past transactions.

### F06 · Faster manual entry

Small changes for the most frequent task:

- *Duplicate* a transaction (from the list or the edit sheet).
- Favourites: saved templates such as "Coffee · 01.1.1 · Cash · 4.50".
- Recently used categories and accounts at the top of each dropdown.
- Arithmetic in the amount field (`12.50+8.30`).
- Keyboard: `N` for a new transaction, `⌘↩` / `Ctrl+Enter` to save.

## Organizing

### F07 · Automatic categorization

Suggest the category (and account) for new or imported transactions.

1. **Rules:** "text contains *Migros* → 01.1 Food". Users create rules from any transaction
   ("Always categorize like this").
2. **Learning from history:** without rules, suggest the category this payee had most often.
   Simple, local and explainable ("used 14 times for this payee").
3. **Optional AI:** for unknown payees, ask an AI model to choose a COICOP code from the
   payee and text. Opt-in, since the text leaves the computer.

Suggestions are always shown and confirmable, never applied silently. Works best with
payees (F09) and pays off most together with imports (F01, F02).
**Decide:** which levels to build; whether confirmed suggestions should create rules
automatically.

### F08 · Simpler category picker

COICOP names are statistical ("Individual consumption expenditure of non-profit institutions
serving households"). Keep COICOP as the storage format, but:

- show short everyday names ("Groceries", "Public transport") with the COICOP code beside them;
- put recent and favourite categories first;
- search everyday words ("Netflix" → 09.4 Recreational services).

### F09 · Payees

A payee or merchant field, normalized ("MIGROS ZUERICH HB 1234" → "Migros").

- Payee list with merge and rename; payee filter and search in Transactions.
- Basis for categorization (F07), subscription detection (F17) and "top merchants" reports.
- Imports fill it from the bank text; QR bills from the creditor.

### F10 · Tags

Labels across categories: "Holiday Italy 2026", "Renovation", "Business".

- Several tags per transaction; filter, total and chart by tag.
- Budget by tag (for example a project budget across many categories).
- Required for the tax summary (F20), where "deductible" is a tag.

### F11 · Split transactions

One payment, several categories: a supermarket receipt with food, household goods and a
gift.

- The transaction keeps one total and account; lines split the amount by category, and
  optionally by tag.
- Reports and budgets count each line in its own category.
- Interacts with advance payments: a split line can be the part paid for someone else.

### F12 · Attachments

Keep receipts, invoices and contracts with the transaction.

- Files are stored in the data folder (`attachments/<transaction id>/`), so backups and sync
  include them.
- Preview in the edit sheet; open with the system viewer.
- Required by receipt scanning (F04); useful for warranty and tax proofs.

### F13 · Bulk edit

Select many transactions → change category, account, tag or note in one step. The selection
bar on the Transactions page already exists; this adds an *Edit* sheet for several rows.

## Planning and insight

### F14 · Savings goals

"Holiday 4'000 CHF by June", "Emergency fund 3 months of spending".

- Target amount and date, optionally linked to an account.
- Progress on the Overview; the monthly amount needed to reach it on time.
- Optionally reserves money in the projection, so the projected free wealth is honest.

### F15 · What-if scenarios

Compare futures: "What if I reduce to 80 % work from January?", "Buy a car for 25'000 CHF in
March".

- A scenario is a set of budget changes applied on top of the plan, never written into it.
- The Overview wealth chart shows the plan and each scenario as separate lines.
- **Decide:** how many scenarios to keep; whether a scenario can be adopted into the plan.

### F16 · Budget from history

Propose a budget from what actually happened: the average (or median) of the last 3, 6 or 12
months per category, as a starting point the user edits. A one-sheet action on Budget Plan.

### F17 · Subscription and recurring-spend detection

Find charges that repeat with similar amounts (streaming, phone, gym) and list them with
the yearly total. One click turns a detected subscription into a recurring transaction (F05).

### F18 · Trends and unusual spending

- Month-over-month and year-over-year change per category on the Budget page.
- A short "Notable this month" list on the Overview: categories well above their usual
  level, a large single transaction, a budget about to be exceeded.

### F19 · Year in review

A yearly report: income, spending by category, savings rate, wealth change, biggest
expenses, comparison with the previous year. On screen and as a PDF, built on the existing
statement export.

### F20 · Swiss tax summary

Help with the annual tax declaration, without trying to file it:

- yearly totals for typically deductible items: professional expenses, pillar 3a payments,
  insurance premiums, medical costs, donations, childcare, debt interest;
- uses COICOP codes where they match and tags (F10) otherwise;
- export as PDF or CSV with the supporting transactions and attachments (F12).

Cantonal rules differ, so the app lists amounts and leaves the judgment to the user.

## Wealth

### F21 · Investments and asset classes

Today wealth comes from recorded account balances. Add:

- asset classes (cash, securities, pillar 2/3a, real estate, crypto, other) and net worth
  by class;
- securities holdings with quantity and price, valued manually or (optional, online) with
  daily prices;
- performance per holding and in total.

Revaluations of the kind "capital gains on investment" (F43) are the manual first step;
F21 would create them from price changes.

### F22 · Loans and mortgages

Liabilities with interest rate and repayment plan; interest payments are categorized
automatically. Projected wealth includes amortization; a mortgage renewal date can trigger
a reminder (F31).

### F23 · Who owes what (shared expenses)

Advance payments already record money paid for others. Build on that:

- an overview per person: open amounts, oldest open item, total settled this year;
- a "settle up" action that records the repayment;
- an optional summary to send (text or PDF), without the app sending anything itself.

## Data out and safety

### F24 · Export to CSV and JSON

- Export the current Transactions or Budget Plan view (with its filters) as CSV or Excel.
- Export everything as one JSON file: accounts, categories, transactions, balances, budget,
  settings, with a format version, readable by other tools.
- **Decide:** whether the JSON export should also be importable (it then doubles as F25).

### F25 · Full backup and restore

- *Back up now* writes a dated ZIP of the data folder to a place the user picks.
- *Restore* replaces the data from a ZIP after confirmation, with an automatic backup of the
  current state first.
- The update backups in `backups/` can be listed and restored from the same screen.

### F26 · Sync through a cloud folder

Use the app on two computers by keeping the data folder in iCloud Drive, OneDrive or
Dropbox.

- Settings gets *Data folder* with a move function.
- The single-instance lock only works on one computer. Two computers editing at the same
  time can overwrite each other's files, so the app needs change detection and a merge
  strategy (per-record changes, which F28's change log enables).
- **Decide:** "one computer at a time" with a warning (simple), or real merging (large).

### F27 · Encryption and app lock

The CSV files are readable by anyone with access to the computer.

- **App lock:** password, or Touch ID / Windows Hello, before the app opens.
- **Encryption at rest:** data files encrypted with a key derived from the password.
- **Trade-off:** encrypted files can no longer be opened in Excel, and a forgotten password
  means lost data, so a recovery key is needed.

### F28 · Undo and change history

- *Undo* for the last action (delete, edit, import), offered in the confirmation toast.
- A change log (who, when, before and after) for transactions, balances and budget entries.
- Makes imports safe to try, and enables merging in F26.

## Experience

### F29 · Multi-language support

Interface in English, German, French and Italian, matching Switzerland and the installer,
which already speaks English, German and French.

- All interface text moves into translation files (gettext); the language follows the
  system or is chosen in Settings.
- Number and date formats per language (`1'234.50` stays for Switzerland; `9. Okt. 2026`,
  `9 oct. 2026`).
- **COICOP:** official names exist in the national languages (Eurostat publishes COICOP 2018
  translations); the shipped `coicop2018.csv` gets one column per language. Everyday names
  (F08) need translating too.
- PDF and Excel statements in the chosen language.
- **Decide:** which languages first; whether user-entered names (accounts, income
  categories) need translations (usually not).

### F30 · Onboarding and demo data

- A first-start guide: language, display currency, accounts with their current balance,
  income categories, plan horizon. It ends on the Budget page with something to see.
- *Try with sample data:* a separate demo data folder to explore without touching real data.

### F31 · Reminders and alerts

Desktop notifications, all optional:

- record month-end balances (first days of the month);
- a budget close to or over its limit;
- recurring payments due (F05), open QR bills (F03), goals off track (F14).

Shown when the app runs; reminders while it's closed would need a small background helper
(larger).

### F32 · Keyboard shortcuts and accessibility

- Shortcuts: new transaction, save, search, previous/next month (`←` `→`), switch pages.
- Screen-reader labels, visible focus, logical tab order, sufficient contrast in both
  appearances (the colour tokens make the contrast checkable).

### F33 · In-app update check

Once a day, read the latest version number from the GitHub repository (the same kind of
read-only request as the ECB download). If it's newer, the sidebar shows "Budget 0.2.0 is
available" with a link to the installer and the update steps. Can be switched off in
Settings; the README's privacy note then needs one more line.

### F34 · Several households or profiles

Separate budgets (personal, joint household, a small association) in separate data folders,
switchable from the sidebar. Builds on the existing `--data-dir` support; mainly a picker and
per-profile settings.

### F35 · Phone companion

Record expenses on the phone where they happen.

- **Smallest step:** the app already runs a web server; a phone-friendly *quick add* page
  reachable on the home network. Needs the app lock (F27), because the server would then
  accept connections from other devices.
- **Larger step:** a phone app that queues entries offline and syncs with the computer.
- **Decide:** whether entries made away from home matter (then a sync service is needed,
  which conflicts with local first).

---

## Known bugs (issue 01)

| ID | Bug | Cause | Effort |
| --- | --- | --- | --- |
| B01 | Excel and PDF buttons in the budget overview do nothing | Downloads are switched off in the app window (pywebview's `ALLOW_DOWNLOADS` is off by default), so the file is dropped without an error. In a browser it works. | S |
| B02 | Chart image, CSV and PDF export don't work | CSV, Excel and image: same cause as B01. PDF: the menu has no PDF entry. | S |
| B03 | Transactions use the latest exchange rate, not the one of their date | Manual entries are valued at the newest ECB rate; only imports use the booking date. Account balances already use the last rate of the month, as asked. | S–M |
| B04 | *Income and expenditure* chart leaves out unaccounted expenditure | The chart totals only recorded categories, while the budget page includes *Unaccounted*. Closed by F42: totals then mean recorded spending everywhere, and the gap can be added to the chart as its own line. | – (F42) |
| B05 | Selecting and adding a chart doesn't work | Not reproduced. The wiring looks right; most likely the new chart is added below the fold with no feedback, or an error is only logged to the console. | S |
| B06 | Currency exchange converts the received amount instead of asking for it | The form has no field for it; it is always calculated at the ECB rate. The data file already has the column. | S |
| B07 | Accounts on the budget page don't adapt to the window size | Fixed-width rows that don't wrap; the account-currency amount wraps, and the name gets squeezed. | S |
| B08 | Income and expenditure should stack when the window is narrow | Today they are always stacked (since the redesign). Read as: side by side on wide windows, stacked on narrow ones. | S |
| B09 | Editing a budget entry on the budget page lacks *Until*, *Repeat* and *Note* | The dialog edits amount and currency only; the service can't change a series' schedule. | M |

**B03 details.** Every transaction gets the ECB rate of its date, or the last earlier rate
on weekends and holidays; future dates get the newest rate. Changing the date revalues the
entry. Stored transactions are recalculated once after the update (backed up first by the
update backup). This replaces the earlier decision, and the specification's rule, that manual
entries use the latest rate. A transaction dated today and entered before the ECB publishes
(around 16:00) gets yesterday's rate; whether to correct it at the next start is a detail to
decide when building it.

**B09 details.** Budget entries are stored one row per month, linked by a series ID. The
dialog shows the series' end month (or *Forever*), its rhythm and an editable note; changing
the schedule rewrites the series' rows from the edited month on. The same controls should
come to the edit dialog on the Budget Plan page.

## From issue 01

### F36 · Closing balance from bank files

Bank exports usually carry a running balance (*Saldo*, *Kontostand*, *Balance*). Today the
import ignores it.

- The bank profile gets a balance column, detected from its name where possible.
- The review step lists "Balance at end of *month*" for each month the file covers to the
  end, and shows any recorded balance it would replace. Accepted ones become the account's
  month-end balance, at the month-end rate.
- A manual *Closing balance* field covers banks that put the balance outside the table.
- Small extras that make categorizing imports quicker: a category editor in each row, and
  "transfer to own account" as a row type.
- **Decide:** what to do with a file that ends mid-month (skip its last balance, or offer it
  with a warning).

### F37 · COICOP information page

A page listing all COICOP categories as a tree with code and name, in the interface
language, with a search box. Linked from the sidebar or Settings. With F40, each category
also shows what it includes.

### F38 · Exchange-rate chart

A fourth chart type on the Overview: the ECB rates of chosen currencies against the display
currency over a period, daily or at month end (the rates balances are valued with). Optional
index view (start = 100) when currencies differ in scale. Exportable like the other charts.

### F39 · COICOP level 5

COICOP 2018 itself has four levels (division, group, class, subclass), and the app already
ships all of them. A fifth level exists only in national extensions.

- Needs a source with a fifth level whose codes keep the COICOP 2018 codes, so saved data
  stays valid; German names for the new codes (and French and Italian later).
- The code is ready for any depth; the work is the data and its translation.
- **Decide:** which source. A Swiss classification from the Federal Statistical Office
  would fit the users best, if it extends COICOP 2018.

### F40 · COICOP notes in search and on hover

The official COICOP 2018 publication describes each category with *includes*, *also
includes* and *excludes* notes. The shipped file has names only.

- Add the notes (about 600) and a German translation of them.
- Category search in every dropdown also matches the notes; hovering an entry shows them.
- The import page's category select switches to the same control.
- **Decide:** where the German notes come from (official translation, if one exists, or our
  own); whether *excludes* is shown too (helps choosing, not asked for).

### F41 · Opening balance and change per account

The budget page shows for each account the balance at the end of the previous month, the
balance at the end of this month and the difference, in the account's currency (and the
closing value in the display currency, as today). Accounts emptied this month stay visible.

### F42 · Reconciliation line instead of *Unaccounted*

*Unaccounted expenditure* stops being a row of the expenditure table. Instead, a line above
the income and expenditure tables shows:

- recorded income minus recorded expenditure,
- the change in account balances,
- the difference between the two (the former *Unaccounted* amount).

Total expenditure then means recorded expenditure on the budget page, in the charts and in
the statement export. The statement gets the reconciliation as its own section. With F45,
revaluations are part of the line.

### F43 · Revaluation transactions

A fifth transaction type for value changes that are neither income nor spending.

- One account; enter either the old and new value or just the difference (positive or
  negative), in the account's currency.
- Two kinds: *Capital gains on currency exchange* and *Capital gains on investment*.
- Revaluations count neither as income nor as expenditure; they appear in their own section
  (F45) and explain account changes (F46).
- **Decide:** whether revaluations count towards *Net income* on the budget page.

### F44 · Exchange spread booked as revaluation

With the received amount typed in (B06), the bank's rate differs from the ECB rate. The
difference becomes a linked revaluation of the kind *Capital gains on currency exchange* on
the receiving account, usually a loss.

- The exchange itself is recorded at the ECB rate; the revaluation brings the receiving
  account to the amount actually received. Together they match the bank statement.
- The revaluation is created, updated and deleted with its exchange and can't be edited on
  its own.
- Existing exchanges have no spread and stay as they are.

### F45 · Revaluations in the budget overview

A third section next to income and expenditure listing the month's revaluations by kind,
with a total, also in the statement export. Optionally as lines in the *Income and
expenditure* chart.
**Decide:** whether the change in value of foreign-currency balances between two month ends
counts as *Capital gains on currency exchange* automatically, or stays in the reconciliation
difference until booked.

### F46 · Unexplained difference per account

Next to each account, the difference between the actual change of its balance and the sum
of its transactions in the month (income, spending, transfers, exchanges, revaluations), in
the account's currency. Shown only when both month-end balances are recorded. A *Book as
revaluation* action fills in F43's form with the difference.

### F47 · Projection uses the higher of actual and budget

For a month that isn't over, the projected wealth takes, for each category, the recorded
amount if it is higher than the budget, and the budget otherwise. Applies to income and
expenditure alike, to the projected wealth on the budget page and to the Overview chart.

- Compared per top-level row (COICOP division, taxes, advance payments, income category),
  so a budget set on a parent category isn't counted twice.
- Advance payments have no budget and use the recorded amount.
- **Decide:** months that are over but have no recorded balances yet: budget only (as
  today) or the same rule.

### Decisions for the product owner

Collected from the entries above; the first two block work in milestone 1 and 2.

1. **B08:** side by side on wide windows and stacked on narrow ones — is that the intent?
2. **F42 / B04:** replace *Unaccounted* by the reconciliation line, with the gap as an
   optional chart line, instead of adding *Unaccounted* to the chart.
3. **F43:** do revaluations count towards *Net income*?
4. **F45:** are value changes of foreign-currency balances automatic currency gains?
5. **F47:** which rule applies to finished months without recorded balances?
6. **F36:** what to do with a bank file that ends mid-month?
7. **F39, F40:** which source for level 5 and for the German notes?

---

## Suggested bundles

| Bundle | Features | Why together |
| --- | --- | --- |
| **Stop typing** | F01, F02, F09, F07 (rules and history), F13 | Imports bring data in; payees and categorization make them usable; bulk edit fixes the rest. |
| **Swiss essentials** | F02, F03, F20, F29 (de, fr, it) | Banking formats, bills, tax and languages of the target users. |
| **Everyday speed** | F06, F08, F05, F32 | Small changes to the most frequent tasks. |
| **Trust** | F24, F25, F28, F27 | Data can leave, come back, be undone and be protected. |
| **Look ahead** | F14, F15, F16, F18 | Planning beyond the current budget. |
| **Books that balance** | F41, F42, F43, F44, F45, F46 | Every change in an account is explained by a transaction or a revaluation (issue 01). |

## Cross-cutting decisions

These come up in several features; deciding them once keeps the features consistent.

1. **AI and online services** (F04, F07, F21, F33): never, opt-in per use, or opt-in as a
   setting? Which provider, and who pays?
2. **Exchange rate of transactions** (F01, F02, F03, F05, B03): decided — every transaction
   uses the ECB rate of its date (the last earlier rate on weekends and holidays); account
   balances use the last rate of the month; budget entries use the latest rate. Changed by
   issue 01: manual entries used the latest rate before.
3. **Data format changes:** payees, tags, splits and attachments add fields and files. Each
   needs a default for old data (the update backup covers mistakes); see
   [packaging/README.md](../packaging/README.md#releasing-an-update).
4. **Languages** (F29): decide before building many new screens, so new text is translatable
   from the start.
