# Budget App – Architecture & Implementation

This document describes how the budgeting app specified in
[Description_Budgeting_App.md](Description_Budgeting_App.md) is implemented: the module
structure, the CSV data model, the calculation rules, and the decisions and assumptions
taken where the specification left room for interpretation.

## 1. Overview

- **Runtime:** Python 3.13. Dependencies are managed with `uv`.
- **UI:** [NiceGUI](https://nicegui.io) 3. Pages are written in Python and served
  locally on `http://127.0.0.1:8080` (localhost only), or in a native window with `--native`.
  Installed builds always use the native window (see §9).
  - Tables use AG Grid.
  - Charts use Plotly.
- **Storage:** plain CSV files plus one `settings.json` in `data/` (configurable).
- **Exchange rates:** the ECB historic reference-rate file, downloaded once per day.
- **Classification:** COICOP 2018, all four levels (15 divisions, 63 groups, 186 classes,
  338 subclasses). It was extracted from the Eurostat SDMX codelist `ESTAT/COICOP18`
  and is bundled as `src/budget_app/resources/coicop2018.csv`.

The code is split into three layers, which keeps every calculation testable without the UI:

```
ui/         NiceGUI pages and widgets  ── only calls ──▶  services/
services/   business rules & calculations ─────────────▶  domain/ + storage/
domain/     pure helpers: months, FX rates, COICOP, record types
storage/    typed CSV tables + settings JSON
```

## 2. Module map

| Module | Responsibility |
| --- | --- |
| `main.py` | CLI entry (`uv run budget`). Registers pages, refreshes ECB rates at startup and every 30 min. |
| `config.py` | Base currency `CHF`, ECB URL, data/resource paths (`BUDGET_DATA_DIR` env override). |
| `domain/months.py` | `YYYY-MM` month arithmetic and the "month complete" rule. |
| `domain/fx.py` | `FxRates` (parse ECB CSV, rate lookup with date fallback, cross-rate conversion) and `FxService` (daily download, atomic file replacement). |
| `domain/coicop.py` | COICOP tree (ancestors, children, labels) plus pseudo-categories `TAX` and `ADV`. |
| `domain/models.py` | Dataclasses `Account`, `IncomeCategory`, `Transaction`, `AccountEntry`, `BudgetEntry`, `Settings`. |
| `storage/csv_store.py` | Generic `CsvTable[T]`: in-memory records, atomic rewrite (temp file, fsync, rename) on every change. Unknown columns are kept and written back. |
| `storage/store.py` | `Store` bundling all tables and settings. Unknown settings keys are kept. |
| `services/items.py` | Product lists of transactions: validation (blank lines ignored, half-filled lines rejected), replace, totals, counts and names for the Transactions list. |
| `storage/backup.py` | On the first start of a new app version, copies the data files to `<data>/backups/` before the store loads them. The version that last opened the folder is in `<data>/.app_version`. |
| `storage/lock.py` | Single-instance lock on `<data>/.lock`, so two processes cannot overwrite each other's files. |
| `services/context.py` | `Context` (store + FX), conversion helpers, budget horizon, `ValidationError`. |
| `services/accounts.py` | Accounts, advance-payment accounts and income categories, with their deletion rules. |
| `services/transactions.py` | Validation and saving of the four transaction types, advance payments and settlement. |
| `services/budget.py` | Budget series expansion, overwrite, horizon change and "Forever" extension. |
| `services/wealth.py` | Account balances, wealth, projected wealth, wealth time series. |
| `services/aggregates.py` | Sums of transactions and budget entries per category and month; `projected_net` (the higher of recorded and budget per category). Shared by reports and the wealth projection. |
| `services/reports.py` | Monthly statement (expenditure tree, income, budget vs. actual, reconciliation, opening balances), chart series. |
| `services/export.py` | Statement as Excel/PDF; chart data and list views as CSV/Excel; every record as one JSON document (`format: budget-app/export`, `version: 1`), not meant to be imported back. |
| `services/archive.py` | Full backup of the data folder's records as a ZIP (with a `budget-backup.json` manifest) and restoring one: plain CSV/JSON names only, size limits, every table must load before anything changes; the current files go to `backups/<timestamp>_before-restore/` first, then the store is reloaded. |
| `services/importer.py` | CSV import: encoding, separator and header detection, column mapping and bank profiles, duplicate marking, category suggestions, saving in one write. |
| `services/camt.py` | camt.053 / camt.054 statements (any version) read into the importer's rows; the account remembered per IBAN. |
| `ui/theme.py` | Design tokens (light and dark), Quasar and AG Grid overrides, chart styling. See `docs/DESIGN_REVIEW.md`. |
| `ui/layout.py` | Sidebar frame, sheets (dialogs), segmented control, month stepper, metrics, formatting (`1'234.50 CHF`), date picker. |
| `ui/widgets/` | Searchable selects, transaction form (page, add sheet and edit sheet), AG Grid helpers. |
| `ui/pages/` | One module per page (see §6). |

## 3. Data model (`data/`)

Every money record stores **the original amount and currency, the amount in CHF, and the
ECB date of the rate used** (`fx_date`).

| File | Columns |
| --- | --- |
| `accounts.csv` | `name, currency, kind (regular/advance), created, id` |
| `income_categories.csv` | `name, id` |
| `transactions.csv` | `date, type, amount, currency, amount_chf, fx_date, category, from_account, to_account, to_amount, to_currency, is_advance, advance_amount, advance_account, debtor, settles_id, comment, import_ref, id` |
| `transaction_items.csv` | `transaction_id, name, quantity, unit_price, position, id` – optional product list of a transaction (e.g. a receipt), in the transaction's currency. Deleted with its transaction. |
| `account_entries.csv` | `account_id, month, amount, currency, amount_chf, fx_date, id` – recorded balance at month end ("budget recording") |
| `budget_entries.csv` | `month, type, category, amount, currency, amount_chf, fx_date, series_id, recur_months, forever, comment, generated_until, id` – one row per budgeted month |
| `settings.json` | `budget_horizon, display_currency, last_fx_download` |
| `ecb/eurofxref-hist.csv` | Latest ECB file (replaced daily) |

**Transaction types**

- `expenditure`
  - `category` is a COICOP code at any level, or `TAX`.
  - `from_account` must be a regular account in the transaction currency.
- `income`
  - `category` is an income-category id.
  - `to_account` must be a regular account in the transaction currency.
- `transfer`
  - `from_account` and `to_account` are different regular accounts in the transaction currency.
- `exchange`
  - `amount` is in the currency of `from_account`.
  - The user types the amount received, in `to_currency` (the currency of `to_account`); the
    form shows the ECB amount and both rates for comparison.
  - `to_amount` stores the **ECB amount** (at `fx_date`). The spread between what was received
    and the ECB amount is a linked `revaluation` of kind `REVAL_FX` on `to_account` (F44); the
    two rows point at each other through `linked_id`. Together they credit exactly what the bank
    did. Saving, editing and deleting the exchange keep the spread booking in step, it can't be
    edited or deleted on its own, and the rate check re-splits it when the exchange's rate
    date moves.
- `revaluation` (F43)
  - A change in an account's value that is neither income nor spending, in the account's
    currency. `category` is the kind: `REVAL_FX` (capital gains on currency exchange) or
    `REVAL_INVEST` (capital gains on investment).
  - `amount` stays positive: a gain is booked to `to_account`, a loss from `from_account`. The
    form takes the change as a signed difference or as old and new values.

**Advance payments.** An advanced expenditure is stored as **one row**:

- `amount` is the full payment.
- `advance_amount` is the share paid on behalf of others.
- `advance_account` and `debtor` record where the receivable sits and who owes it.

If no advance account is chosen, the account "Advance payments" in the transaction currency
is used, and it is created automatically if needed.

A **repayment** is an `income` row with `is_advance=1`, the advance account, and
`settles_id` pointing to the advanced expenditure. What is still outstanding on an
advance is `advance_amount − Σ amount of its repayments`. A repayment can never exceed
that outstanding amount.

## 4. Calculation rules

### 4.1 Currency conversion

ECB rates are EUR-based (`1 EUR = r[CUR]`, with `r[EUR] = 1`). Every conversion is a cross-rate on the same date:

```
amount_to = amount_from / r[from] · r[to]
```

A rate lookup for date *d* uses the latest ECB date ≤ *d*, which covers weekends and
holidays. Dates before the start of the file use its first rate.

| Use | Rate used |
| --- | --- |
| Saving a transaction (`amount_chf`), manual or imported | Last ECB date on or before the transaction date (the latest rate for future dates); stored as `fx_date`. Editing re-values it only when amount, currency or date change |
| After each rate check (start, then every 30 minutes) | Transactions whose `fx_date` is not the last ECB date on or before their date are re-valued: entries saved by older versions (latest rate), entries made before the ECB published that day, future-dated entries. Exchange target amounts are kept |
| Saving a budget entry (`amount_chf`) | Last (most recent) row of the ECB file; that date is stored as `fx_date` |
| Saving an account entry | Last ECB date within that month |
| Displaying a transaction in another currency | Rate of its recorded `fx_date` |
| Displaying an account balance | Last ECB date of the month being viewed |
| Displaying budget entries | Latest available rate |

### 4.2 Wealth

- **Wealth(M)** is the sum over **regular** accounts of the latest account entry with
  month ≤ M, so balances carry forward. **Advance accounts are excluded.** They are
  receivables tracked as open items (decision §7.1).
- **Current wealth** is Wealth(previous month). It is shown on the Overview and the Budget
  overview whatever month is selected.
- **Projected wealth(M):** let L be the last month with at least one account entry. Then
  - projected wealth(M) = Wealth(L) + Σ expected net for months L+1 … M;
  - a month's **expected net** (`aggregates.projected_net`, F47) takes, per top-level
    expenditure row (COICOP division, taxes, advances) and per income category, the
    **higher of recorded and budgeted**: overspending or extra income that is already
    recorded counts, and a budget not yet used still counts. Advances and repayments have
    no budget and count as recorded. The rule applies to every month after L, including
    months that are over but have no balances yet (decision 5);
  - for M ≤ L it is simply Wealth(M). Without any balances the projection starts at 0 in the
    first month with a budget entry or a transaction.

### 4.3 Monthly statement (`reports.month_report`)

- **Month complete:** month < current month. For complete months the Budget overview lists
  the accounts. For the current and future months it shows the projected wealth instead,
  and budget cells become editable.
- **Expenditure tree:** a category's amount is its own direct entries plus everything
  recorded in any descendant code. For example, `01` includes `01`, `01.1`, `01.1.1`
  and `01.1.1.1`.
  - Top-level rows: the 15 COICOP divisions, **Taxes** and **Advance payments** (the
    `advance_amount` shares).
  - An advanced expenditure contributes `amount − advance_amount` to its COICOP code and
    `advance_amount` to *Advance payments*.
- **Income:** non-zero income categories, plus **Advance repayments** (the repayments).
- **Reconciliation** (F42, replaces the former *Unaccounted Expenditure* row): for complete
  months with balance data for M−1, the Budget page shows above the tables
  - recorded net = total income − total expenditure,
  - change in balances = Wealth(M) − Wealth(M−1),
  - **revaluations** = booked revaluations of the month (at their own rates) + the automatic
    exchange-rate effect on foreign balances (decision 4): per account, opening × (rate at
    the end of M − rate at the end of M−1) + Σ each movement × (rate at the end of M − its own
    rate), in the view currency,
  - **unexplained** = change in balances − recorded net − revaluations. A negative value is
    money that left the accounts without being recorded; a positive one arrived without a
    record. With every movement recorded it is 0 in any view currency (tested). The *Income
    and Expenditure* chart offers it as the series *Unexplained difference*.
- **Revaluations** (F45): a section after the expenditure table lists *Capital gains on
  currency exchange* (booked, plus the automatic exchange-rate effect, shown separately in a
  note) and *Capital gains on investment*, with a total. They are not part of *Net income*
  (decision 3).
- **Total expenditure** = recorded expenditure only, on the page, in the charts and in the
  statement export.
- **Account balances** (F41, F46): for each account the opening balance (end of M−1, carried
  forward), the closing balance, the change and the **unexplained** part of the change
  (change − the month's recorded movements on the account), in the account's currency; the
  unexplained part needs a balance recorded for M, and *Book as revaluation…* opens a
  prefilled revaluation for it. The total row shows Wealth(M−1), Wealth(M), their change and
  the month's unexplained difference in the view currency.
- **Difference** = actual − budget. It is shown in red when expenditure exceeds the
  budget or income falls short of it.

### 4.4 Budget series

- **Creating** an entry expands the month range into one row per month, every
  `recur_months` months (1 = monthly, 12 = yearly). All rows share a `series_id`.
  - The final month is limited to the **budget horizon**.
  - "Forever" means "until the horizon".
- **Overwrite** deletes all budget rows of the same type and category in the target months
  before saving.
- **Extending the horizon** continues every `forever` series in its recurrence step, using
  the series' latest row as the template.
  - Generation resumes after the furthest month the series was ever generated for. That
    month is stored in `generated_until`.
  - Months the user deleted or overwrote are therefore never re-created or duplicated.
- **Shrinking the horizon** never deletes anything. Rows beyond the new horizon stay, and
  the series resume after them when the horizon is extended again.

## 5. Exchange-rate refresh

At startup, and every 30 minutes while the app runs, `FxService.refresh_if_stale` compares
`settings.last_fx_download` with today. If they differ, it:

1. downloads the ZIP, capped at 20 MB, with a 100 MB cap on the contained CSV;
2. validates the CSV in memory (nothing is extracted to disk by name);
3. atomically replaces `data/ecb/eurofxref-hist.csv` through a unique temp file;
4. stores today's date.

A lock prevents overlapping refreshes.

If the download fails, the previous file stays in use and a warning banner is shown. The
currency dropdowns list every currency the ECB quoted on its latest date, plus EUR and CHF.

## 6. Pages

| Route | Page | Notes |
| --- | --- | --- |
| `/` | Overview (start page) | Current wealth and the projection at the horizon in one hero card. Chart builder ("Add Chart" menu): wealth line (recorded, then dotted projection), income/expenditure bars or lines with category multi-select and optional budget, waterfall of per-account change. Each chart's "…" menu saves a PNG, exports CSV or Excel, or removes it. |
| `/budget` | Budget | Month stepper (‹ month ›) and currency. A callout names accounts without a balance for a past month. Metric strip (current wealth, wealth or projected wealth at month end, net income). Income and expenditure ledgers share one column layout: budget meter, actual, budget, difference; zero values show as a dash. The expenditure tree expands by clicking a category. Budget amounts are editable (pencil on hover) in incomplete months: amount, currency, until (or forever), repeat and note per entry; changing until or repeat replans the series from that month on (`budget.reschedule`), the earlier months keep their values and end there. Balances list for past months. Statement export sits in the share menu. "Record Balance" opens the balance sheet. |
| `/add-transaction` | New Transaction | The same form also opens as a sheet from the sidebar's "New Transaction" button on every page; the page behind refreshes after saving. Type first (segmented control), then amount, currency and date, then the type's fields. Account dropdowns are filtered by currency. Live CHF preview. Save stays disabled until the form validates, and the reason is shown once the user has started. After saving, every field except date, currency and type resets. Expenditures and income can carry a product list (item, quantity, unit price; negative prices for discounts): Enter on a price starts the next line, item names are suggested from earlier lists, and the list's total is compared with the amount ("Use Total as Amount") but never changes it on its own. |
| `/transactions` | Transactions | Search field, filters (date range, amount range, account, type) behind "Filters". AG Grid with signed, coloured amounts, the number of items per transaction, sort on every column. The search also finds item names. Selecting rows reveals Edit and Delete; double-click edits in a sheet. "Import…" opens the CSV import. The share button exports the rows the filters show as CSV or Excel. |
| `/import` | Import Transactions | 1 · file: encoding (UTF-8, Windows-1252), separator and header line (bank preambles are skipped) are detected. 2 · columns: account, date column and format, one signed amount or debit/credit columns, decimal style, description columns, sign inversion for card statements; the mapping can be saved per bank (`import_profiles.json`) and is recognised by the header line next time. 3 · review: rows already imported (same `import_ref`) or matching a manual entry (same account, day, type and amount) are left out; categories are suggested from earlier transactions with the same text; selected rows get a category, are included or excluded in bulk. Saving is one write; nothing is saved while a row is invalid. **camt.053 / camt.054 (XML, F02):** recognised by content, read namespace-agnostically (versions .001.02 to .001.08 and later) with the standard library after a pre-scan that refuses any DTD (so no entities), capped at 20 MB like CSV. The columns step is skipped: one row per booked `Ntry` (pending entries are left out and counted), booking date, signed amount (`CdtDbtInd`), text from counterparty name, `Ustrd` and `AddtlNtryInf`; a batch whose `TxDtls` each carry an amount adding up to the entry becomes one row per detail. `import_ref` is `camt:<IBAN>:<AcctSvcrRef \| NtryRef \| content hash>`, so the same or an overlapping statement shows its entries as already imported; a row matching a manual entry or one imported from the other file kind (CSV ↔ camt) on the same day, type and amount is a possible duplicate. The account is chosen in the review step, preselected from `import_accounts.json` (IBAN → account id, written after a successful import); a statement whose currency differs from the account's is refused. The statement's IBAN and period are shown; its closing balance is parsed (`Statement.closing_balance`) but not used yet (F36). |
| `/add-budget` | New Budget Entry | Also a sheet on Budget Plan. Type, amount, currency, category, month range or "Forever", repeat interval, replace option, note. Live summary of the months that will be created. |
| `/budget-entries` | Budget Plan | Same list pattern as Transactions, with the same export. The edit sheet can apply changes to all later months of a series, or replan it with Until and Repeat. |
| `/settings` | Settings | Grouped lists: interface language (system, English, Deutsch, Français, Italiano), plan horizon (Apply appears when changed), display and base currency, accounts, advance-payment accounts (with outstanding balances), income categories. Add, edit and rename open small sheets. *Data*: back up now (ZIP), restore from a backup file, export everything as JSON, the data folder's path; *Automatic Backups* lists the update and before-restore copies with a Restore action. |

Pages follow the system light/dark appearance (`ui.run(dark=None)`).

### Languages

- English is the source language. Texts in the code are wrapped in `t()` (with `{placeholders}`),
  plurals in `tn()`, and texts in constant tables are marked with `N_()` and translated where shown
  (`i18n.py`). Where one English word needs different German words, `tc(context, text)` looks up
  `"context|text"` first (`tc("heading", "Income")` → "Einnahmen"; the transaction type "Income"
  stays "Einnahme").
- Translations live in `resources/i18n/<lang>.json` for German, French and Italian (English text
  → translation; plural texts map to `[singular, plural]`). COICOP names are in
  `resources/i18n/coicop_<lang>.json`, keyed by the English name. They are the app's own
  translations (German in Swiss spelling, no "ß"; French and Italian follow the Eurostat/ISTAT
  wording where known), not certified official texts.
- The language is a setting (`settings.json` → `language`; empty means follow the system:
  `LANG`, macOS `AppleLanguages`, or the Windows locale). Changing it reloads the page. Quasar
  (date picker) uses `de-CH` / `fr` / `it` / `en-GB`, AG Grid gets `localeText` per language, and
  dates in the grids use the browser's `de-CH` / `fr-CH` / `it-CH` / `en-GB` formatting. Amounts
  keep the Swiss format in every language. The Windows installer speaks the same four languages.
- `tests/test_i18n.py` fails, for each language, when a marked text has no translation, when
  placeholders differ, when a translation is no longer used, or when a COICOP name is missing.

## 7. Decisions and assumptions

1. **Advances sit outside wealth** (agreed during planning). Advanced shares appear as the
   expenditure line *Advance payments*, and repayments count as income. This keeps
   the reconciliation consistent, because the receivable is not also counted
   in wealth.
2. **An advance is a single row** with an `advance_amount` column, not two linked rows.
   Editing and deleting stay atomic, and the reports split the amount.
3. **An exchange records the amount actually received** (issue 01, B06): the ECB part on the
   exchange, the bank's spread as a linked revaluation (F44). Exchanges saved before F44 keep
   their full received amount on the exchange; their spread is split off when they are next
   edited, not automatically, because older entries were valued at a different rate.
4. **Balances carry forward.** A month with no entry for an account reuses the account's
   last recorded balance. The Budget overview flags such accounts and offers an "Add"
   button.
5. **Deleting an account** is refused while transactions reference it. Its recorded
   balances are deleted along with it, and the confirmation dialog says so. The currency of
   an account in use cannot be changed.
6. **Repayments** can only be entered in the currency of their advance account. A deleted
   advance requires its repayments to be deleted first, or selected for deletion at the
   same time.
7. **Chart PNGs** are rendered in the browser (Plotly `downloadImage`), so no headless
   Chrome or Kaleido is required.
8. **Default budget horizon:** December of next year, until it is changed in Settings.
9. **Localhost only.** The server binds to `127.0.0.1` because the app has no login. Use
   `--host 0.0.0.0` only on a trusted network.
10. **Exports are formula-safe.** Text cells in Excel and CSV exports that start with `=`,
    `+`, `-`, `@`, a tab or a carriage return are prefixed with `'`. A name like
    `=HYPERLINK(...)` therefore cannot run as a formula. The raw `data/*.csv` files are not
    escaped, because they are the app's own storage. Open them in a spreadsheet only with
    care.
11. **Edits keep the original valuation.** Editing a transaction keeps its `amount_chf` and
    `fx_date` unless the amount or currency changes.
12. **Advances with repayments are locked.** The currency and advance account of an advanced
    expenditure cannot change once repayments exist, and its advance share cannot drop
    below what has already been repaid.
13. **Storage safety:**
    - Every write is fsynced before the file is replaced.
    - `get()` returns a copy, so a failed update never leaves memory and disk out of sync.
    - An unparsable value aborts startup with the file and line number, instead of
      silently dropping data.

## 8. Testing

- `uv run pytest` runs the unit tests (`tests/`) for:
  - FX parsing, cross-rates and date fallback;
  - COICOP level counts and roll-up;
  - every transaction type, the advance split, settlement and deletion rules;
  - budget expansion, overwrite, horizon extension and shrinking, and no resurrection of
    deleted or overwritten months;
  - CSV round-trip, corrupt-file reporting and formula-safe exports;
  - CSV import: detection of encoding, separator, header line, columns, number and date
    formats; duplicates on re-import; booking-date valuation; saved profiles;
  - camt.053 / camt.054 import: versions .001.02/.04/.08 and unknown namespaces, batch entries,
    pending entries, bank and content references, refused DTDs and non-camt XML, the IBAN memory
    and the currency check;
  - translation completeness and language switching;
  - edit valuation and locking of repaid advances;
  - wealth with carry-forward, projected wealth (higher of recorded and budget) and the reconciliation.
- The UI was exercised end-to-end with Playwright against a seeded data set:
  - all pages and the forms with advance and repayment;
  - the grid filters, edit and multi-delete;
  - horizon extension and budget-cell editing;
  - balance recording;
  - the Excel, PDF, CSV and PNG exports.

## 9. Installers

Installed builds are made with PyInstaller (see [packaging/README.md](../packaging/README.md)):

- **macOS:** `Budget.app` inside a drag-to-Applications `.dmg`.
- **Windows:** an Inno Setup installer. It installs per user, needs no admin rights, and
  adds a Start-menu entry and an uninstaller.

An installed build:

- bundles Python and all dependencies, so neither uv nor Python is needed;
- opens in a native window (pywebview: WebKit on macOS, WebView2 on Windows) on a free port;
- keeps its data in the user data folder:
  - macOS: `~/Library/Application Support/Budget`
  - Windows: `%APPDATA%\Budget`

  The data survives updates and uninstalls. `BUDGET_DATA_DIR` or `--data-dir` override the
  location.
