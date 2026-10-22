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
- **QR bills (F03):** PDFs are rendered with PDFium (`pypdfium2`), photos opened with Pillow, and QR codes
  found with ZXing (`zxing-cpp`); all on the computer, nothing is sent.
- **Classification:** COICOP 2018, all four levels (15 divisions, 63 groups, 186 classes,
  338 subclasses). It was extracted from the Eurostat SDMX codelist `ESTAT/COICOP18`
  and is bundled as `src/budget_app/resources/coicop2018.csv`. What each category includes, also
  includes and excludes (F40) comes from the UN Statistics Division's COICOP 2018 structure file
  (`resources/coicop_notes.json`, made by `scripts/coicop_notes.py`; see "Languages" in §6).

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
| `main.py` | CLI entry (`uv run budget`). Registers pages, refreshes ECB rates at startup and every 30 min, then books what automatic recurring transactions have due. |
| `config.py` | Base currency `CHF`, ECB URL, data/resource paths (`BUDGET_DATA_DIR` env override). |
| `domain/months.py` | `YYYY-MM` month arithmetic and the "month complete" rule. |
| `domain/fx.py` | `FxRates` (parse ECB CSV, rate lookup with date fallback, cross-rate conversion, `history()` for the rate chart) and `FxService` (daily download, atomic file replacement). |
| `domain/coicop.py` | COICOP tree (ancestors, children, labels) plus pseudo-categories `TAX` and `ADV`; `fold()` makes text case- and accent-insensitive for searching. F40: `notes(code, lang)` → `Notes` (tuples of items for `includes`, `also_includes`, `excludes`, and `translated`) or None, read on first use and cached; a language whose file is missing or marked `_untranslated` gets English, and so does a text the translation lacks. `note_words(code, lang)`: the folded words of includes and also includes, in the language and in English, each once (excludes is never searched). `match_words(query, primary, secondary)`: whether every word occurs in either, and whether in the first. `notes_payload(lang)` / `notes_version(lang)`: all notes for the browser and a short hash of them. |
| `domain/everyday.py` | F08. Everyday names and search words of the common categories (`resources/everyday_categories.json`) and the category picker's options: `category_options(recent)` lists the recently used codes first, then COICOP order with Taxes last; each option has its label (`Groceries · 01.1`, or the COICOP label for the others), the official name as caption, its level, a folded `search` text (code, everyday names in all four languages, official name in the interface language and in English, search words) and `note_search`, the note words (F40). `matches()`: every query word must occur in `search` or `note_search`; `search()` lists the options that match by `search` alone first, then those that need the note words. |
| `domain/payees.py` | F09. Payee names from bank texts, deterministic and offline: `clean_words` drops dates, times, card, reference, terminal and phone numbers, IBANs and postal codes, the booking words around the merchant (UBS, PostFinance, ZKB, Raiffeisen, Revolut, TWINT; German, French, Italian, English: "Einkauf", "Kauf/Dienstleistung vom", "Paiement à", "Card payment to" …) and payment services in front of it (`TWINT *`, `SumUp *`, `PAYPAL *`); `normalize` names a brand from `resources/payees.json` (longest pattern first; banks and payment services only when nothing else matches) or keeps the rest without the location (a legal form at the end stays, spelled AG, GmbH, SA, Sàrl, Sagl …; decision 11), title-cased when the bank wrote capitals (acronyms such as SBB stay); `alias_key` is the folded cleaned text (location kept) a bank text is remembered by; `key` folds a name for comparing (case, accents and punctuation ignored). |
| `domain/tags.py` | F10. The tags column of transactions, templates and series: tag ids separated by `\|` (`split`, `join`), `clean_name` (single spaces, no `\|`) and `key` (case, accents and spacing ignored); `PENDING` marks a name that becomes a tag only when its record is stored. |
| `domain/splits.py` | F11, pure. The one place that decides how an expenditure's amount is counted per category (§4.3, decision 21): `Line` (an item line's total, category and tags), `Part` (category, amount, amount_chf, tags), `is_split` (a category other than the transaction's, or tags of its own), `parts(amount, amount_chf, category, lines, advance, tags)` (whole cents and micro-CHF, so the parts add up exactly), `split_total`, `rest` and `overflow`. |
| `domain/rules.py` | F07. Which categorization rule applies: `match_key` (folded text, single spaces), `by_priority` (longest text first, then the newest), `first_match` / `best` (the first rule of the type whose text occurs in the payee or the bank text / note). |
| `domain/models.py` | Dataclasses `Account`, `IncomeCategory`, `Transaction`, `AccountEntry`, `BudgetEntry`, `Recurring`, `Payee`, `Rule`, `Tag` (with the palette `TAG_COLOURS`), `Holding`, `Trade`, `Price`, `Attachment` (with its `folder`), `Bill` (F03), `Settings`; the asset classes (`ASSET_CLASSES`, `HOLDING_CLASSES`) and trade kinds. |
| `domain/investments.py` | F21, pure. A holding's trades and prices as `Event`s in the order they count (`events`: by date, a day's opening holding first, then its trades in entry order, then its typed or pasted price), `replay` (per event the quantity before and after, the previous and new price, the money moved and the revaluation, and the booked value round(quantity × price, 2)), `oversold`, `value_on`, `price_date`, `performance` (`Performance`: value, opening value, bought, sold, net dividends, withholding tax, fees, average cost, realized; money put in, gain, %, unrealized; `scaled` and `+` for totals), `default_withholding` (35 % in CHF), and the price-list helpers `date_format`, `parse_day`, `parse_number`. |
| `domain/attachments.py` | F12, pure. The accepted file types (`TYPES`: extension, content type, preview kind and a check of the first bytes; `file_type` needs both to agree), `display_name` (the last path part, no control characters, at most 200 characters), `stored_name` / `is_stored_name` / `is_id` (plain names only), `MAX_BYTES` (20 MB). |
| `domain/qrbill.py` | F03, pure. The Swiss QR-bill payload (SIX Implementation Guidelines 2.3, version `0200`): `parse(text)` → `QrBill` (IBAN, creditor and debtor `Address` of type S or K, amount as `Decimal` or None, currency, reference type and reference, message, bill information, alternative procedures) or `QrBillError` (an `N_` message with its values, `translated()`); the checks `iban_valid` (CH/LI, mod 97), `is_qr_iban` (institution id 30000–31999), `qr_reference_valid` / `mod10_check_digit` (recursive mod 10), `creditor_reference_valid` (ISO 11649); `is_payload`, `format_iban`, `format_reference`; `swico(bill_info)` (Swico S1: invoice number and date, customer reference, VAT number, payment terms; `\/` escapes) and `due_date` (invoice date + the 0 % term). See §7, decision 22. |
| `storage/csv_store.py` | Generic `CsvTable[T]`: in-memory records, atomic rewrite (temp file, fsync, rename) on every change. Unknown columns are kept and written back. |
| `storage/store.py` | `Store` bundling all tables and settings. Unknown settings keys are kept. |
| `services/attachments.py` | Attachments (F12): `check` (empty, over 20 MB, type), `add` (file first, then the record), `add_to_bill` (a QR bill's file, F03), `remove`, `remove_for` (with a deleted transaction; a bill's file goes back to its bill instead), `remove_for_bills`, each one undoable action; `list_for_bill`, `ids_ok` (plain ids for the folder and the file), `list_for`, `counts` and `names_by_transaction` (the Transactions list), `path` (built from plain ids, inside `attachments/` or None), `read`, `exists`, `kind`, `size_label`, `system_open` (`open`, `xdg-open` or `os.startfile`) and `cleanup` (files no record and no undo can bring back; at startup). |
| `services/items.py` | Product lists of transactions: validation (blank lines ignored, half-filled lines rejected, a line's category must be a COICOP code or `TAX`), `check_split` (lines with their own category or tags may not exceed the amount, F11), replace (a line keeps its category and tags only on an expenditure; new tag names become tags), totals, counts and names for the Transactions list. |
| `services/qrcodes.py` | F03. `texts(name, data)`: the QR codes of a PDF (every page up to 20, rendered grayscale at scale 3, about 216 dpi, then once more at scale 6 when none was found) or a photo (JPEG, PNG, WebP; EXIF orientation), in page order, each once, decoded as UTF-8; HEIC and other types are refused with a message. The libraries are imported when a file is read, not at startup. |
| `services/bills.py` | F03 (decision 15). `read` (the valid bills of a file and the reasons for refused codes; a notice of 0.00 is refused), `scan` (adds new bills with the file as attachment; refuses a file without a usable or new bill), `from_payload`, `bill_key` (duplicates), `open_bills` (by due date, undated last), `closed_bills`, `due` / `due_count` (overdue or due within 7 days, the sidebar badge), `payee` (`payees.resolve` of the creditor), `note`, `payment_input` (the sheet's prefill), `suggestion` (F07), `mark_paid`, `check_payment` / `paid_with` (the sheet's way), `release` (a deleted payment opens its bill again), `dismiss`, `reopen`, `set_due`, `delete`, `payment`; every write one undoable action. |
| `storage/backup.py` | On the first start of a new app version, copies the data files to `<data>/backups/` before the store loads them, and hard-links the attachment files there (copies where links fail; they never change once written). The version that last opened the folder is in `<data>/.app_version`. |
| `storage/lock.py` | Single-instance lock on `<data>/.lock`, so two processes cannot overwrite each other's files. |
| `storage/history.py` | Change history and undo (F28): every table write records the record before and after (as written to the CSV file); changes made together form one *action* (`history.action(label)`, nested ones join the outer). Undo reverts an action in reverse order after checking every record still looks as the action left it. Kept in `history.jsonl` (the newest 500 actions), not part of backups. |
| `services/context.py` | `Context` (store + FX), conversion helpers, budget horizon, `ValidationError`. |
| `services/accounts.py` | Accounts (with their asset class, F21), advance-payment accounts and income categories, with their deletion rules. |
| `services/transactions.py` | Validation and saving of the four transaction types, advance payments and settlement; deleting releases the QR bills a deleted transaction paid (F03); `recent_categories()` (the last ten distinct expenditure categories, for the category picker). An edit is `prepare_edit` (validate and build the new record, nothing saved) then `store_edit` (save it, keeping an exchange's spread booking in step); `save` does both. |
| `services/bulk.py` | Bulk edit (F13): `BulkChanges` (expenditure category, income category, payee, account, tags to add and to remove (F10), note mode and text; `None` or an empty list leaves a field as it is), which field applies to which row (`takes_*`, `is_spread`), `plan()` (every selected row as form input with the changes applied, checked with `prepare_edit`; rows the changes leave as they are drop out, also when the built record equals the stored one) and `bulk_edit()`, all or nothing (`BulkEditError` names the failing rows by date and amount) as one recorded action. Adding a field means one attribute, one `takes_*` rule and one line in `apply()`. |
| `services/budget.py` | Budget series expansion, overwrite, horizon change and "Forever" extension. |
| `services/recurring.py` | Recurring transactions (F05): series, their schedule (`occurrence`, `next_after`), the due occurrences, `book` / `skip` / `book_all` through `transactions.save`, and `book_automatic` for series that book without asking. |
| `services/wealth.py` | Account balances (a securities or crypto account's from its holdings, F21), wealth, wealth by asset class (`by_class`), projected wealth, wealth time series. |
| `services/investments.py` | F21. Holdings (add, rename, delete with everything they booked), trades (`TradeInput`, `validate_trade`, `save_trade`, `delete_trade`) and prices (`save_price`, one per holding and day; `delete_price`; `price_effect` previews one), price lists (`read_price_list` reads, `save_prices` saves), each one undoable action. `_book` books a holding's linked transactions again from all its trades and prices after every change (§4.6). `value_entry` is a securities or crypto account's month-end balance, `account_value`, `performance`, `history`, `source` (the trade or price of a booked row, or the converted account), `reclaimable_tax` (withholding tax on CHF dividends per year). Conversions (decision 13): `needs_conversion`, `check_conversion` (rules and preview), `convert_account` (one undoable action), `book_conversion` (the opening difference as a linked revaluation). |
| `services/aggregates.py` | Sums of transactions and budget entries per category and month; `projected_net` (the higher of recorded and budget per category). Shared by reports and the wealth projection. F11: `split_lines` (the item lines with a category or tags, per transaction, read once per sum), `transaction_parts` (a stored transaction's `domain.splits` parts; only expenditure is split), `part_value` (a part in the view currency, converted like `tx_value`), `expenditure_parts` and `is_split`. Every sum by category or tag goes through `transaction_parts`. |
| `services/reports.py` | Monthly statement (expenditure tree, income, budget vs. actual, reconciliation, opening balances), chart series. |
| `services/export.py` | Statement as Excel/PDF; chart data and list views as CSV/Excel; every record as one JSON document (`format: budget-app/export`, `version: 1`), not meant to be imported back. |
| `services/archive.py` | Full backup of the data folder's records and attachment files as a ZIP (with a `budget-backup.json` manifest; version 2 when it holds attachments, else 1) and restoring one: plain CSV/JSON names and `attachments/<id>/<id>.<ext>` paths only, at most 100 record files, 20,000 attachments of 20 MB each and 2 GB in all, every table must load, every attachment record must name plain ids with an accepted extension and every attachment file must pass the content check before anything changes; the current files go to `backups/<timestamp>_before-restore/` first (the attachments moved there), then the store is reloaded and the attachment files its records name are written. `create` refuses a backup `read` would refuse. |
| `services/undo.py` | `@recorded(label)` names a service function's action; `undo()` with readable errors; labels and one-line change descriptions for the history page (holdings, trades and prices included). |
| `services/templates.py` | Transaction templates (F06): save, list, delete, apply to a form input; exchanges and revaluations are excluded. |
| `services/payees.py` | Payees (F09): `resolve(raw)` (an alias or name in the table first, then `normalize`; never a second spelling of a stored payee), `ensure(name)` (the stored spelling, or a new payee), `learn(pairs)` (imports: the payee plus the text's alias, taken from any other payee), `names` (recently used first, for the form), `usage` (count, spent and received per payee), and `rename`, `merge`, `delete` (unused payees only), each one undoable action that rewrites transactions, templates and series together. |
| `services/tags.py` | Tags (F10): `encode(entries)` (tag ids or names → a tags column, new names pending, nothing written), `store(column)` (creates the pending tags), `ensure`; `add`, `rename`, `recolour`, `merge` (records move to the tag kept), `delete` (the tag comes off every transaction, template and series), `set_budget`, each one undoable action; `tagged_amounts` (the money that counts per tag: own share of expenditure spent per part, so a split line's tags count that line (F11), income other than repayments received), `transaction_tags` (a transaction's tags and its lines'), `totals(view, start, end)` and `budget_status(tag)`. See §6, *Tags*. |
| `services/rules.py` | Categorization rules (F07): `validate` (text of at least two characters, expenditure or income, a category of that type, an optional regular account), `save` (refuses a second rule with the same folded text and type unless asked to replace it), `delete`, `same_text`, `category_label` and `prefill` / `from_transaction` (the payee, or the name `normalize` finds in the note, with the category); each change one undoable action. |
| `services/categorize.py` | Category suggestions (F07): `Suggester` reads the rules and the history once and answers `suggest(type, payee, text, account=None)` with a `Suggestion` (category, account, translated reason, source) or None; `suggest(ctx, …)` is the one-off shortcut. Writes nothing. See §6, *Category suggestions*. |
| `services/importer.py` | CSV import: encoding, separator and header detection, column mapping and bank profiles, duplicate marking, category suggestions (`categorize`, marked on the row with their reason; `clear_suggestions`), saving in one write. |
| `services/camt.py` | camt.053 / camt.054 statements (any version) read into the importer's rows; the account remembered per IBAN. |
| `services/import_balances.py` | Month-end balances from a bank file (F36): the order of a CSV balance column (newest or oldest first) and the balance after each month's last booking, or a camt closing balance; compared with recorded balances and saved with `wealth.upsert_entry`. |
| `ui/theme.py` | Design tokens (light and dark), Quasar and AG Grid overrides, chart styling. See `docs/DESIGN_REVIEW.md`. |
| `ui/layout.py` | Sidebar frame, sheets (dialogs), segmented control, month stepper, metrics, formatting (`1'234.50 CHF`), date picker. |
| `ui/widgets/` | Searchable selects, the payee field (`selects.PayeeInput`: a `ui.input` with the component `resources/payee_input.js`, a Quasar select used as text autocomplete, so the typed text is the value and the known payees, recently used first, are suggested while typing, matched anywhere and ignoring case and accents), transaction form (page, add sheet and edit sheet), AG Grid helpers. `selects.CategorySelect` is the category picker (F08): a `ui.select` subclass whose component `resources/category_select.js` (NiceGUI's `select.js` with another filter and option template) filters on each option's pre-folded `search` text in the browser, so typing is instant and needs no round trip; options show the official name as a second line and a history icon when recently used. F40: the filter also accepts the option's `note_search` words and lists those matches after the matches by name (the test helper `offered()` in `tests/ui/test_category_picker.py` mirrors it); the component loads the notes once per page from `notes-url` (`/coicop-notes/<lang>?v=<hash>`, served by `selects.add_notes_route()`, cached immutably) when the picker first opens, shared by every picker on the page, and shows an option's notes in a `q-tooltip` on hover (after 350 ms) or when the arrow keys reach it; on touch screens (`$q.platform.has.touch`) an info button in the option opens them inside the option instead, and stops its tap from selecting. Used by the transaction form, both budget forms, the Budget Plan edit sheet, the bulk edit sheet and the import page (with the income categories as extra options). The value stays the COICOP code (or `TAX`). `bulk_edit.BulkEditSheet` is the bulk edit sheet (F13): one method per field, re-planned with `bulk.plan` on every change. `rule_sheet.open_rule_sheet` adds or edits a categorization rule (F07), `always_like_this` opens it from a transaction. |
| `ui/widgets/tags.py` | F10. `TagSelect`: a multiple `ui.select` whose chosen tags are chips in their colour (Quasar slots `selected-item` and `option`), a name typed with Enter added as it is (a new tag once saved; a typed name of an existing tag picks it, ignoring case); `chip`, `ColourPicker` (one swatch per palette colour) and `CHIPS_RENDERER`, the AG Grid cell renderer that builds the chips as elements from the row's `_tags`, so names are never HTML. |
| `ui/widgets/attachments.py` | F12. `AttachmentList`, the *Attachments* section of the transaction form (see §6), and the route `/attachment/{id}` (`register`, `serve`, `url`). |
| `ui/pages/` | One module per page (see §6). |

## 3. Data model (`data/`)

Every money record stores **the original amount and currency, the amount in CHF, and the
ECB date of the rate used** (`fx_date`).

| File | Columns |
| --- | --- |
| `accounts.csv` | `name, currency, kind (regular/advance), created, asset_class, invest_from, id` – `asset_class` (F21): `cash`, `securities`, `pillar2`, `pillar3a`, `real_estate`, `crypto` or `other`; files written before it have no such column, which reads as `cash`. Advance-payment accounts are always `cash`. `invest_from` (decision 13): the month (`YYYY-MM`) an account with history became a securities or crypto account; empty for accounts that were one from the start, and in older files. |
| `income_categories.csv` | `name, id` |
| `transactions.csv` | `date, type, amount, currency, amount_chf, fx_date, category, from_account, to_account, to_amount, to_currency, is_advance, advance_amount, advance_account, debtor, settles_id, comment, import_ref, linked_id, recurring_id, payee, invest_id, tags, id` – `recurring_id` names the recurring transaction that booked the row (F05; empty in files written before it); `payee` is the name of a payee in `payees.csv` (F09; expenditure and income only, empty in older files); `invest_id` is the id of the trade or price in `trades.csv` / `prices.csv` that booked the row (F21; empty otherwise); `tags` are the ids of the row's tags in `tags.csv`, separated by `\|` (F10; empty in older files and for spread bookings) |
| `transaction_items.csv` | `transaction_id, name, quantity, unit_price, position, category, tags, id` – optional product list of a transaction (e.g. a receipt), in the transaction's currency. Deleted with its transaction. `category` (F11): a COICOP code or `TAX` of a line of an expenditure, empty for the transaction's; `tags`: tag ids separated by `\|`, counted for the line on top of the transaction's. Files written before F11 have neither column: every line keeps the transaction's category. |
| `templates.csv` | `name, type, amount, currency, category, from_account, to_account, comment, payee, tags, id` – saved starting points for new transactions (F06); `amount` 0 means none. |
| `attachments.csv` | `transaction_id, name, stored, size, content_type, added, bill_id, id` – a file kept with a transaction (F12) or a QR bill (F03): `name` as it was added (only shown), `stored` the file's name on disk, `<id>.<extension>`, in `attachments/<bill_id or transaction_id>/`; `size` in bytes. A bill's file has `bill_id` and an empty `transaction_id` until the bill is paid, then both (§7, decision 22); files written before F03 have no `bill_id` column. Removed with its transaction (a bill's file goes back to the bill); the file outlives the record while an undo can restore it (§7, decision 20). |
| `bills.csv` | `creditor, iban, currency, amount, creditor_address, reference_type, reference, message, bill_info, due, status, transaction_id, added, id` – one row per scanned QR bill (F03): `amount` 0 when the bill leaves it to the payer, `creditor_address` one line ("Rue du Lac 1268, 2501 Biel, CH"), `reference_type` `QRR`, `SCOR` or `NON`, `bill_info` as on the bill (Swico syntax or free), `due` `YYYY-MM-DD` or empty, `status` `open`, `paid` or `dismissed`, `transaction_id` the expenditure that paid it. |
| `payees.csv` | `name, aliases, id` – one row per payee (F09). `aliases` are lookup keys separated by `\|`: the cleaned, folded bank texts imported for the payee (`migros zuerich hb`) and its former names (`migros` after a rename). See §7, decision 15. |
| `rules.csv` | `match, type, category, account, id` – one row per categorization rule (F07): the text looked for (compared folded), `expenditure` or `income`, the category (COICOP code or `TAX`, or an income category id) and an optional account suggested with it (the From account of expenditure, the To account of income). Rows are in the order they were added; the later row is the newer rule. |
| `account_entries.csv` | `account_id, month, amount, currency, amount_chf, fx_date, id` – recorded balance at month end ("budget recording") |
| `budget_entries.csv` | `month, type, category, amount, currency, amount_chf, fx_date, series_id, recur_months, forever, comment, generated_until, id` – one row per budgeted month |
| `recurring.csv` | `type, amount, currency, start, next_due, category, from_account, to_account, comment, recur_months, end, auto, payee, tags, id` – one row per recurring transaction (F05): the template (expenditure, income or transfer), the first date, the rhythm in months, the optional last date (`end`, empty for none), `auto` (book without asking) and `next_due`, the first occurrence not yet booked or skipped |
| `holdings.csv` | `account_id, name, symbol, id` – a security or coin in a securities or crypto account (F21), in the account's currency; `symbol` is an optional ISIN, ticker or symbol. |
| `trades.csv` | `holding_id, date, kind, quantity, price, fee, amount, tax, cash_account, comment, seq, id` – `kind` is `buy`, `sell`, `dividend` or `opening` (the holding a converted account starts with, decision 13); quantity and price have up to 6 decimals (0 for a dividend), `fee`, the dividend's gross `amount` and its withholding `tax` 2 (decision 12; files without the column read as tax 0, so an older dividend's amount is its gross); `cash_account` is the regular account paid from or to, in the holding's currency (empty for an opening holding); `seq` is the order of entry (trades of one day count in it). |
| `tags.csv` | `name, colour, budget, budget_currency, budget_start, budget_end, id` – one row per tag (F10). `colour` is a key of the fixed palette (`grey`, `red`, `orange`, `yellow`, `green`, `teal`, `blue`, `purple`, `pink`; shades for light and dark mode in `ui/theme.TAG_SHADES`); `budget` 0 means none; `budget_start` / `budget_end` are `YYYY-MM-DD` or empty for an open end. Records name tags by id, so a rename changes this file only. See §7, decision 19. |
| `prices.csv` | `holding_id, date, price, id` – a price typed in or pasted (F21), one per holding and day. |
| `settings.json` | `budget_horizon, display_currency, last_fx_download` |
| `attachments/<transaction or bill id>/<id>.<ext>` | The attachment files (F12; a QR bill's in the bill's folder, F03), named by the app, never by the user. |
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

**Rows booked by investments (F21)** carry `invest_id`: transfers, fees (expenditure), dividends
(income) with their withholding tax (expenditure in `TAX`), and *Capital gains on investment*
revaluations made from a trade or price (§4.6); the revaluation of a conversion (decision 13) carries
the account's id. They are never saved, edited or deleted on their own: `transactions.validate` and
`delete` refuse them, bulk edit leaves them out, and the transaction editor opens their trade or price
instead (or explains the conversion). Securities and crypto accounts take no other transactions
(`transactions._account` refuses them) and no recorded balances (`wealth.upsert_entry` refuses them) –
a converted account still does for dates and months before its conversion month (`Account.holds_in`).

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
- A **securities or crypto account** (F21) has no recorded entries: its balance at the end of M is
  computed from its holdings, Σ round(quantity × latest price on or before the month end, 2)
  (`investments.value_entry`, used by `wealth.balance_entry`), from the month of its first trade or
  price on (opening 0 in that month). It never needs an entry, and the first recorded month counts
  its first trade or price; the projection still starts after the last month with a *recorded*
  balance, so trades and price updates don't move it.
- A **converted** account (decision 13, `invest_from` set) keeps its recorded balances for the months
  before its conversion month (`Account.holds_in(month)` is false there, so `balance_entry` carries its
  entries forward as for any account); from the conversion month on it is valued from its holdings.
  `AccountRow.valued` tells the pages which way a row was valued that month.
- **By asset class** (`wealth.by_class`): Wealth(M) summed per account's asset class.
- **Current wealth** is Wealth(previous month). It is shown on the Overview and the Budget
  overview whatever month is selected.
- **Projected wealth(M):** let L be the last month with at least one account entry. Then
  - projected wealth(M) = Wealth(L) + Σ expected net for months L+1 … M;
  - a month's **expected net** (`aggregates.projected_net`, F47) takes, per top-level
    expenditure row (COICOP division, taxes, advances) and per income category, the
    **higher of recorded and budgeted**: overspending or extra income that is already
    recorded counts, and a budget not yet used still counts. Advances and repayments have
    no budget and count as recorded. A split transaction (F11) is recorded per part, each in
    its own division (§4.3). The rule applies to every month after L, including
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
  - **Split transactions** (F11, decision 21; `domain.splits.parts`, used through
    `aggregates.transaction_parts` by every sum by category or tag): an item line of an expenditure with
    a category other than the transaction's, or with tags of its own, is a *split line*. It counts its
    total in its category (or the transaction's, for a line with tags only), in the original currency
    and in CHF at the transaction's own rate (`amount_chf / amount`), rounded to the cent; split lines
    with the same category and tags form one part. The **rest** – the amount minus the split lines
    (lines without a category, deposits, rounding, anything not itemised) – stays in the transaction's
    category with its tags and takes whatever makes the parts add up to `amount` and `amount_chf`
    exactly; when nothing is left of the amount, that CHF rounding goes to the largest split part. A
    negative split line (a discount) lowers its category and raises the rest by as much. Split lines may
    not exceed the amount (refused when saving). With an advance, every part gives the same fraction
    `advance_amount / amount` to *Advance payments*, rounded to the cent per part with the difference on
    the rest, so *Advance payments* gets exactly the advance share and each category its own share.
    In another view currency a part is converted from its CHF amount like the transaction (`part_value`).
    Only expenditure is split. Totals, account flows and the reconciliation use whole transactions,
    which the parts add up to, so a split never changes them.
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

### 4.5 Recurring transactions (F05)

- **Occurrences** fall on the day of the series' `start`, every `recur_months` months; a day past
  the end of a shorter month becomes its last day (31 Jan → 28 Feb → 31 Mar). An occurrence
  after `end` does not exist.
- **Due** are the occurrences from `next_due` up to today, one per date, so missed months show
  individually. They are handled in order: *Book* and *Skip* act on the earliest one of a series
  and move `next_due` to the next occurrence.
- **Booking** builds a `TransactionInput` from the series (amount and date can be changed) and
  saves it with `transactions.save`, so it is validated and valued like a transaction typed in,
  and sets `recurring_id`. A series that can't be booked (an account was changed by hand, no
  rate) is left due with its later occurrences.
- **Automatic** series are booked by `book_automatic` after each rate check (startup, then every
  30 minutes) and when they are saved; booking advances `next_due`, so it never books twice.
- **Editing** a series changes its future occurrences only: transactions it booked keep their
  values. The edited series continues after the last occurrence it booked or skipped, on the new
  schedule (from the new first date if it has handled none). Deleting a series keeps its
  transactions.
- **Projection:** series are not part of the budget projection; a budget entry still plans them,
  which avoids counting the same rent twice.

### 4.6 Investments (F21)

- **Order:** a holding's trades and prices count by date; on one day its trades come first, in the
  order they were entered (`seq`), and a price typed in or pasted for that day last (a price list
  quotes the close). A buy's or sale's price is also a price of its day.
- **Booked value:** after every event the investment account holds exactly round(quantity × latest
  price, 2) for the holding. A buy books a transfer of round(quantity × price, 2) from the cash
  account, a sale the other way; every price (a trade's included) books the rest of the change as a
  linked *Capital gains on investment* revaluation (to the account for a gain, from it for a loss):

  ```
  revaluation = round(held after × price, 2) − round(held before × previous price, 2) − money moved in
  ```

  which is quantity × (new price − previous price) up to the cent, without rounding errors piling up
  over many events. A fee is an expenditure from the cash account in COICOP 12.2.9.9 (*Other
  financial services n.e.c.*); a dividend is income of its **gross** amount to the cash account in the
  income category *Dividends* (found by name in any language, else added in the interface language; a
  renamed one stays in use for that dividend), and its **withholding tax** an expenditure in *Taxes*
  (`TAX`) from the same account, both linked to the trade: the account changes by the net (decision 12,
  §7 decision 18). The sheet prefills the tax with 35 % of the gross in CHF accounts (Swiss withholding
  tax) and 0 otherwise, until it is typed in; an edited dividend keeps its tax.
- **Reclaimable withholding tax** (`investments.reclaimable_tax`): the tax on dividends of holdings in CHF
  accounts, summed per calendar year; the page lists this year and the two before, the holding sheet
  shows it in each dividend's history line (foreign tax is shown as such and not summed).
- **Rebooking:** every change to a holding's trades or prices (add, edit, delete, paste, rename,
  back-dated or not) replays all of them in order and changes only the linked rows that differ
  (`investments._book`): rows keep their ids, and their CHF valuation while amount, currency and date
  stay; rows of a trade or price that no longer exists go. It runs inside the user's action, so the
  change and its bookings are one undo step.
- **Checks:** the cash account is a regular, non-investment account in the holding's currency
  ("exchange the money first"); no sale may sell more than is held at that point, including after
  an edit, a delete or a move to another holding; dates may not be in the future.
- **Performance** (`domain.investments.performance`): money put in = opening value + bought − sold;
  gain = value − money put in + net dividends (gross − withholding tax) − fees; % = gain / money put in
  (none when nothing is put in any more). The withholding tax is shown apart.
  Realized = sales − the average cost of what was sold; unrealized = value − the average cost of
  what is held; realized + unrealized = value − money put in. Totals on the page convert each
  holding at the latest rate into the display currency.
- **Converting an account with history** (decision 13, `investments.convert_account`, one recorded
  action): the user picks the conversion month M (not in the future; default the current month) and the
  opening holdings (name, ISIN/symbol, quantity, price). Refused while the account has a transaction dated
  on or after the first day of M or an active recurring series. The account gets the class and
  `invest_from = M`; its recorded balances from M on are deleted (the sheet says so), those before stay.
  Each holding starts with an `opening` trade on the **first day of M** – chosen over the last day of
  M−1 so the opening and its revaluation fall in M, and M−1 keeps reconciling from its recorded balance.
  An opening moves its value into the holding like a buy but books no transfer (the money was in the
  account already), and counts as money put in, so it creates no gain. The difference
  `Σ round(quantity × price, 2) − last balance recorded before M` (0 without one) is booked as one
  *Capital gains on investment* revaluation on the first day of M with `invest_id` = the account's id
  (`investments.book_conversion`); it is rebooked whenever an opening holding changes or a balance before
  M is recorded. Reconciliation of M: opening = the recorded balance of M−1, flows = that revaluation +
  the holdings' bookings, closing = the holdings' value, so the unexplained part is 0 (tested in CHF and
  in a EUR account in several view currencies). Trades and prices before the first day of M are refused.
  Changing back to another class is refused while holdings exist (with their trades and prices); without
  them it clears `invest_from` and removes the revaluation, and the recorded balances carry forward again.
- **Price lists:** one line per price, cells split by tab, else semicolon, else comma; date, holding
  and price (the last cell); the date format most lines use (ISO, `dd.mm.yyyy`, `dd.mm.yy`,
  `dd/mm/yyyy`, `yyyymmdd`); a first line that isn't a date is a header. A price may be written
  `97.40`, `97,40`, `1'234.50`, `1.234,50` or `CHF 97.40`. The holding is found by ISIN/symbol or
  name, folded. A repeated holding and day: the later line counts. Reading writes nothing.

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
| `/` | Overview (start page) | Current wealth and the projection at the horizon in one hero card; below it, once any account has another asset class than cash, *By Asset Class* (F21): wealth at the end of last month as one bar split by class, with each class's amount and share. Chart builder ("Add Chart" menu): wealth line (recorded, then dotted projection), income/expenditure bars or lines with category multi-select and optional budget, waterfall of per-account change, ECB exchange rates of chosen currencies in the display currency (daily or month end, optionally indexed to 100), wealth by asset class (stacked bars per month, F21), spending by tag (one bar per tag in its colour, the own share of tagged expenditure between two months, most first, F10). Each chart's "…" menu saves a PNG, exports CSV or Excel, or removes it. |
| `/budget` | Budget | Month stepper (‹ month ›) and currency. A callout names accounts without a balance for a past month. Metric strip (current wealth, wealth or projected wealth at month end, net income). Income and expenditure ledgers share one column layout: budget meter, actual, budget, difference; zero values show as a dash. The page may grow to 100 rem; the two ledgers sit side by side once their container is 1,500 px wide (each about 750 px), else below each other (B08, decision 1). Container queries on each ledger drop the meter from 800 px and the meter and difference below 560 px. The expenditure tree expands by clicking a category. Budget amounts are editable (pencil on hover) in incomplete months: amount, currency, until (or forever), repeat and note per entry; changing until or repeat replans the series from that month on (`budget.reschedule`), the earlier months keep their values and end there. Balances list for past months. Statement export sits in the share menu. "Record Balance" opens the balance sheet. |
| `/add-transaction` | New Transaction | The same form also opens as a sheet from the sidebar's "New Transaction" button on every page; the page behind refreshes after saving. Type first (segmented control), then amount, currency and date, then the type's fields. Account dropdowns are filtered by currency. Live CHF preview. Save stays disabled until the form validates, and the reason is shown once the user has started. After saving, every field except date, currency and type resets. Expenditures and income can carry a product list (item, quantity, unit price; negative prices for discounts): Enter on a price starts the next line, item names are suggested from earlier lists, and the list's total is compared with the amount ("Use Total as Amount") but never changes it on its own. F11: each line of an expenditure has a split button (grey, blue once the line has its own category or tags) that opens a row below it with the category picker (`CategorySelect`, "Same as the transaction" while empty) and a tag field for that line; a line that has them shows them when the form opens, and the pickers are built only then, since each carries every category. Below the list, "Rest in 01.1 Food: 23.40 CHF" (or "the transaction’s category" before one is chosen) shows what stays in the transaction's category; when lines with their own category exceed the amount it says so in red, and Save refuses it with the same message. For income the split buttons are hidden, and lines that still carry a category say they lose it when saved. Faster entry (F06): the amount fields take sums (`12.50+8.30`, evaluated safely, shown as the result when the field is left); a templates menu beside the type saves and applies named templates (`templates.csv`); recently used accounts come first; ⌘↩ / Ctrl+Enter saves, and N opens a new transaction on every page; *Duplicate* in the edit sheet and the Transactions selection bar copies a transaction (with its items) dated today. Expenditures and income have a *Payee* field (F09) below the date: free text with the known payees as suggestions (recently used first); a new name becomes a payee when saved. Templates and duplicates carry the payee. A new expenditure or income with a payee and no category shows the suggested category under the category field (F07): "Use Groceries · 01.1" and the reason ("Used 14 times for this payee", "Rule: contains “migros”", plus the account when one would be filled in); a click fills the category and, if still empty, the account. The edit sheet offers *Always Categorize Like This…* there instead. Under the note, *Attachments* (F12, every type): a drop zone (drop files, or click to choose several; the picker offers the accepted types, and the uploader refuses larger files before sending them), then per file a thumbnail (photos) or icon, name, size, date and *Open*, *Save a Copy…*, *Remove*; clicking the name of a photo or PDF toggles a preview below the list (an `<img>`, or the PDF in an `<iframe>`, which WKWebView, WebView2 and Qt WebEngine render; headless Playwright WebKit leaves it blank). In the edit sheet each file is stored or removed at once, with its own Undo toast, and the list behind the sheet refreshes; a new transaction keeps the files in memory and stores them inside its save action, so they share one Undo. |
| `/transactions` | Transactions | Search field, filters (date range, amount range, account, payee, type) behind "Filters". AG Grid with the payee (F09), signed, coloured amounts, the number of items per transaction, a paper clip with the number of attachments (F12; the header shows the clip, exports say *Attachments*), the rhythm of the recurring transaction that booked a row (*Repeats*), sort on every column. The search also finds item names, payees and attachment names. A split expenditure (F11) shows its categories as "01.1 Food + 2 more" (its own category first), the search finds every category of its lines, and the *Tags* column and the tag filter include its lines' tags. Selecting rows reveals Edit (one row: the editor; several: the bulk edit sheet, F13, see below), *Make Recurring…* (one expenditure, income or transfer without an advance: a series that continues it, with the transaction as its first booking), *Always Categorize Like This…* (one expenditure or income with a category: the rule sheet, F07) and Delete; double-click edits in a sheet. "Import…" opens the CSV import. The share button exports the rows the filters show as CSV or Excel. A *Tags* column (F10) shows each row's tags as coloured chips (an AG Grid cell renderer; sorting, the column filter and the export use the names as text); *Filters* has a tag filter (rows with any of the chosen tags), the search finds tag names, and `/transactions?tag=<id>` (from the Tags page) opens the list filtered by that tag. |
| `/recurring` | Recurring | F05. In the sidebar after Transactions; the sidebar shows the number of due occurrences as a badge. *Due*: one row per due occurrence, oldest first, with title, date and type, the amount and date editable, *Book* and *Skip* (later occurrences of the same series wait for the first); *Book All* books every due row with the amounts and dates as edited. *Series*: title, amount, rhythm, next date or *ended*, end date, *books automatically*, last booking, note; edit and delete per row. The sheet (new, edit, *Make Recurring…*) has type (expenditure, income, account transfer), amount and currency, category, accounts (filtered by currency), first date, repeat, until, *Book automatically* payee (expenditure and income) and note, and says when the next occurrence falls; a series with a payee is titled "*payee* · *category*"; saving an automatic series books what it has due in the same undoable step. |
| `/bills` | Bills | F03. In the sidebar after Recurring (icon `o_request_quote`), with a badge counting the open bills that are overdue or due within 7 days (`layout.BADGES`; Recurring has the other). A drop zone ("Drop QR bills (PDF or photo)", or click to choose; a hidden `ui.upload` like the attachments' one) scans each file with `bills.scan` in the event loop (about 0.1 s for a PDF page; PDFium is not thread-safe) and reports what it added, what was already in the list and why a code was refused, prefixed with the file name. *Open*: one row per open bill, the earliest due first: creditor and amount (or "Amount to fill in (CHF)"), the due date ("Overdue since …" in red, "Due today" and "Due on …" within a week in orange, "No due date"), message and formatted reference, the file as a link (`/attachment/<id>` in a new tab; the system viewer in the app window); *Mark Paid…* opens the transaction sheet titled *Pay Bill* with `bills.payment_input` and `bill_id`: the form suggests the category (F07), lists the bill's file under Attachments ("Attached when you save", without Remove), its button is *Mark Paid*, it checks `bills.check_payment` before saving and calls `bills.paid_with` inside its save action; a calendar menu sets or clears the due date; Dismiss and Delete (with a confirmation saying what happens to the file). *Paid and Dismissed · n bills*: a collapsed `ui.expansion` with *Show Payment* (the edit sheet of the payment) or *Reopen*, and Delete. Every action is undoable from its toast. |
| `/import` | Import Transactions | 1 · file: encoding (UTF-8, Windows-1252), separator and header line (bank preambles are skipped) are detected. 2 · columns: account, date column and format, one signed amount or debit/credit columns, decimal style, description columns, an optional balance column (detected from names such as *Saldo*, *Kontostand*, *Balance*, *Solde*), sign inversion for card statements; the mapping can be saved per bank (`import_profiles.json`) and is recognised by the header line next time. 3 · review: rows already imported (same `import_ref`) or matching a manual entry (same account, day, type and amount) are left out; rows without a category get the suggested one (F07), in italics with the reason in a *Suggestion* column, counted in the summary ("2 categories are suggestions (in italics)") and removed together with *Clear Suggestions*; a category applied by hand loses the mark, and changing a row's payee replaces its suggestion; the *Payee* column (F09) shows `payees.resolve` of the counterparty column (a description column named *Empfänger*, *Payee*, *Merchant*, *Bénéficiaire* …) or of the text, and can be edited (double-click); importing adds missing payees and remembers each text as an alias of the payee it was imported with; selected rows get a category, are included or excluded in bulk. Saving is one write; nothing is saved while a row is invalid. **camt.053 / camt.054 (XML, F02):** recognised by content, read namespace-agnostically (versions .001.02 to .001.08 and later) with the standard library after a pre-scan that refuses any DTD (so no entities), capped at 20 MB like CSV. The columns step is skipped: one row per booked `Ntry` (pending entries are left out and counted), booking date, signed amount (`CdtDbtInd`), text from counterparty name, `Ustrd` and `AddtlNtryInf`, the payee from the counterparty name (`RltdPties`, else the text); a batch whose `TxDtls` each carry an amount adding up to the entry becomes one row per detail. `import_ref` is `camt:<IBAN>:<AcctSvcrRef \| NtryRef \| content hash>`, so the same or an overlapping statement shows its entries as already imported; a row matching a manual entry or one imported from the other file kind (CSV ↔ camt) on the same day, type and amount is a possible duplicate. The account is chosen in the review step, preselected from `import_accounts.json` (IBAN → account id, written after a successful import); a statement whose currency differs from the account's is refused. The statement's IBAN and period are shown. **Month-end balances (F36):** below the grid, the review lists "Balance at end of *month*" with a checkbox and any recorded balance it would replace. From a CSV balance column (`ImportRow.balance`), the order of the lines is the reading (file order or reversed) in which the dates run forward and each balance equals the previous one plus the amounts in between (lines without a balance are bridged); the balance after the last booking of each month covered, months without bookings included, is offered. A month is complete, and ticked, when the file continues past it or its last booking is on the month's last day; the month the file ends inside is offered unticked with "the file ends on *date* — not a month-end balance" (decision 6). When no reading fits, or both fit with different results (one-day files), nothing is offered and the reason is shown. A camt statement offers its closing booked balance (`CLBD`, in the statement's currency), ticked when dated on a month's last day. A balance already recorded with the same amount starts unticked. After the transactions are saved, ticked balances are recorded with `wealth.upsert_entry` (month-end rate, replacing the month's entry); the button counts both ("Import 0 Transactions and 1 Balance" when every row is a duplicate). |
| `/rules` | Categorization Rules | F07. Not in the sidebar: *Rules…* on the Payees page and in Settings lead here, and the sidebar highlights Payees. One group per type (expenditure, income) sorted by text: "Contains “migros”" with the category and the account it suggests (or that the category or account no longer exists), edit and delete per row, *Add Rule…* per group and *New Rule*. The sheet has type, *Text contains*, category, optional account; when another rule has the same text and type it says so and the button becomes *Replace Rule*. Every change is undoable from its toast. |
| `/payees` | Payees | F09. In the sidebar after Categories. One group sorted by name: per payee its number of transactions and what was spent and received (all time, display currency); the search matches names and remembered bank texts. Rename (pencil) opens a sheet; ticking two or more reveals a selection bar with *Merge…*, whose sheet chooses the name to keep (the most used is preselected); payees no transaction, template or series names have a delete button. Each action is one undoable step with an Undo toast. |
| `/tags` | Tags | F10. In the sidebar after Payees (icon `o_sell`). One group sorted by name: per tag its chip (a click opens Transactions filtered by it), its number of transactions and what was spent and received (all time, display currency), and with a budget a meter and "540.00 of 2'500.00 CHF · 1'960.00 CHF left · 1 Jul 2026 – 31 Jul 2026" (red when over). *New Tag* and the pencil open a sheet with name and colour swatches; the piggy bank opens the budget sheet (amount, currency, optional From and Until, *Remove Budget*); delete asks first and says how many transactions lose the tag; ticking two or more reveals *Merge…*. Every change is one undoable step. |
| `/investments` | Investments | F21. In the sidebar after Tags. Buttons *Buy*, *Sell*, *Dividend*, *Update Price* and *Paste Prices…* (once a holding exists). A metric strip (value, money put in with dividends and fees, gain and %, in the display currency) and a caption with the formula; one group per securities or crypto account (name, currency, class) with a row per holding: name, ISIN/symbol, quantity × last price on its date, value, gain and %; a total row with two or more holdings; *Add Holding…*. Without such accounts it points to Settings. A holding row opens the holding sheet: its figures (quantity, last price, value, money put in, dividends, fees, realized, unrealized, gain), *Buy*/*Sell*/*Dividend*/*Update Price* for it, the history newest first with the gain or loss each event booked and edit/delete per trade or price, *Edit Holding…* and *Delete Holding…*. The trade sheet (segmented Buy/Sell/Dividend, holding, date, quantity, price, fee or amount received, cash account in the holding's currency, preselected from the last trade, note) says what it books and why Save is disabled ("Only 10 held on …"); the price sheet previews the value and the change; the paste sheet lists each line as read, with the lines left out and why. Opening a booked row in the transaction editor opens its trade or price sheet. |
| `/add-budget` | New Budget Entry | Also a sheet on Budget Plan. Type, amount, currency, category, month range or "Forever", repeat interval, replace option, note. Live summary of the months that will be created. |
| `/budget-entries` | Budget Plan | Same list pattern as Transactions, with the same export. The edit sheet can apply changes to all later months of a series, or replan it with Until and Repeat. |
| `/categories` | Categories | F37. All 602 COICOP 2018 categories (four levels) as one Quasar tree (`ui.tree`), built once: code and name in the interface language, divisions bold, the number of subcategories, and what was spent in each this calendar year (display currency, every level summed up). The search box matches code, name (interface language and English) and, since F40, the note words, every word ignoring case and accents, like the picker: Python searches `search_index()` (`coicop.match_words`), sets the tree's `filter` prop to the matching codes between spaces and a `:filter-method` keeps those nodes in the browser (Quasar keeps the ancestors of every match); the ancestors are put into `expanded`, so typing changes two props instead of rebuilding rows, and the nodes carry no search text. Clearing the search restores what was open before. `TAX` and `ADV` are not COICOP and appear only in a note below the tree. F40: a node with notes has `has_notes`; its info button emits `notes`, and the page adds the notes (`notes_view`: headings with items, "Unofficial translation" for a translation) to that node, or removes them, so only open nodes carry notes. A footnote names the UN source. |
| `/settings` | Settings | Grouped lists: interface language (system, English, Deutsch, Français, Italiano), plan horizon (Apply appears when changed), display and base currency, accounts (with the asset class in the account sheet and the caption, F21; a securities or crypto account shows its value in holdings), advance-payment accounts (with outstanding balances), income categories. Add, edit and rename open small sheets. *Data*: back up now (ZIP), restore from a backup file, export everything as JSON, the data folder's path; *Automatic Backups* lists the update and before-restore copies with a Restore action. |
| `/history` | Change History | The newest 200 actions with time, name, number of changes and the changes in detail; Undo per action (an undone action is marked, and undoing an undo redoes it). Reached from Settings → Data. Confirmation toasts after saving or deleting transactions, budget entries, balances and accounts carry an Undo button for that action. |

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
- Everyday category names (F08) live in `resources/everyday_categories.json`: code → `en`, `de`,
  `fr`, `it` and `keywords` (one list for all languages: shops, brands, everyday words). Sorted by
  code and written like the translation files; `tests/test_everyday.py` checks that every entry has
  all four names and that every code exists.
- COICOP notes (F40) live in `resources/coicop_notes.json` (English, the UN's text) and
  `resources/coicop_notes_<lang>.json` (the app's own, unofficial translations): code →
  `includes`, `also_includes`, `excludes`, each a string with one item per line (empty where the
  UN gives nothing), metadata keys starting with `_` first (`_source` with publication, URL and
  retrieval date; `_translation`; `_untranslated: true` while a file is still an English copy),
  then the codes sorted, written like the translation files. `scripts/coicop_notes.py` rebuilds
  the English file from the UN workbook, reports codes without notes and the source's codes the
  app lacks, and (re)writes untranslated copies; translated files are left alone. They are loaded
  on first use, not at startup. `tests/test_coicop_notes.py` checks every code, the known empty
  ones, the format, and that the German, French and Italian files have the same codes, the same
  number of items per field and the same code references (German without "ß").
- The language is a setting (`settings.json` → `language`; empty means follow the system:
  `LANG`, macOS `AppleLanguages`, or the Windows locale). Changing it reloads the page. Quasar
  (date picker) uses `de-CH` / `fr` / `it` / `en-GB`, AG Grid gets `localeText` per language, and
  dates in the grids use the browser's `de-CH` / `fr-CH` / `it-CH` / `en-GB` formatting. Amounts
  keep the Swiss format in every language. The Windows installer speaks the same four languages.
- `tests/test_i18n.py` fails, for each language, when a marked text has no translation, when
  placeholders differ, when a translation is no longer used, or when a COICOP name is missing.

### Category suggestions (F07)

- **Order:** rules first, then history. Without a rule or a history, nothing is suggested; there is no AI (decision 8).
- **Rules** (`rules.csv`): a rule applies to a transaction of its type when its text, folded (`coicop.fold`: case and
  accents ignored, spaces collapsed), occurs anywhere in the folded payee or bank text / note. Of the matching rules
  the one with the **longest text** wins, being the most specific ("migros restaurant" before "migros"); between
  equally long texts the **newest** (the one added last). Rules whose category no longer exists are skipped.
- **History:** the category the payee (compared by `payees.key`) had most often among stored transactions of the
  same type; between equally frequent ones the most recently used. Transactions stored before payees (F09) have
  none, so for them the same bank text without its digits counts instead ("Used 3 times for this text").
- **Account:** only when the caller leaves it open (`account=None`): an import has its account, the form may not. It
  is the rule's account when the rule names one, else the account the payee had most often, and only a regular
  account that still exists. The form fills it only if it fits the form's currency and the field is empty.
- **Speed:** `Suggester` builds dictionaries from one pass over the transactions (about 15 ms for 5'000) and then
  answers each row in well under a millisecond; an import builds one per file, the form one when it opens and
  again after each save.
- **Never silent, never a rule:** the form fills a suggestion only on a click; the import preview marks it until
  the user changes it, and importing confirms what the preview shows. Confirming creates no rule; only *Always
  Categorize Like This…* or the Rules page do. Suggesting writes nothing (no payee is added).

### Bulk edit (F13)

- *Edit {n} Transactions* has a field per change, each empty ("Leave unchanged") at first and
  shown only when some selected row takes it: the expenditure category for expenditure, the income
  category for income other than repayments (both appear for a mixed selection), the payee of
  expenditure and income (F09; a new name becomes a payee only when saved), the account (the
  From account of expenditure, the To account of income; only accounts in the rows' currency, and
  disabled with the currencies named when the rows mix them), and the note (*Unchanged*, *Replace*,
  *Append* with a space, *Clear*). A hint under each field counts the rows it leaves as they are.
- Each change is re-planned (`bulk.plan`): every row's form input (`edit_input`) gets the changes
  that apply, unchanged rows drop out, the others are checked with `prepare_edit`, the same path as
  a single edit, so advance-payment and repayment rules hold. Failing rows are listed in the sheet
  and Save stays disabled; `bulk_edit` plans again when saving and saves nothing if a row fails.
- Linked rows: an exchange's spread booking is never changed on its own (it keeps its note and is
  re-split by `store_edit` when its exchange is saved); exchanges, transfers and revaluations keep
  their accounts. Booked rows keep `recurring_id`; valuation, items and `import_ref` are kept.
- The whole edit is one action in the change history (*Edit several transactions*); the toast
  "Changed {n} transactions" offers Undo.
- A split transaction (F11) changes only its own category, the rest's; its lines keep theirs, and a hint under
  the category field says how many selected rows are split. *Add Tags* and *Remove Tags* change the
  transaction's tags, not its lines'.

### Tags (F10)

- The transaction form has a *Tags* field above the note for every type (a spread booking is edited through its exchange and stays untagged); the recurring sheet has one too, and templates, *Duplicate*, *Make Recurring…* and booked occurrences carry the tags. Existing tags are picked from the list; a new name, typed and confirmed with Enter, shows as a dashed chip and becomes a tag when the transaction is saved, in the same undo step. Checking the form or planning a bulk edit writes nothing.
- Bulk edit has *Add Tags* (new names allowed) and *Remove Tags* (the tags the selected rows have); they apply to every row except spread bookings and rows booked on the Investments page.
- Totals and budgets count the own share of tagged expenditure as *spent* (the part paid for others is an advance that comes back) and tagged income other than repayments as *received*; transfers, exchanges and revaluations count as tagged transactions without money in or out. A transaction with two tags counts fully for each. A tag's budget compares the *spent* within its period (each end optional) with the budget, converted at the latest rate like budget entries; income does not reduce it. A split line's tags (F11) count that line's part (its own share, with an advance) on top of the transaction's tags; a transaction counts for a tag when it or one of its lines carries it, so the Tags page, the Transactions filter and deleting or merging tags include the lines.

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
14. **Recurring transactions are booked, not generated** (F05, decided by the product owner):
    an occurrence becomes a transaction only when it is booked from the Due list, or by a
    series set to *Book automatically*. Editing a series changes its future occurrences only;
    the transactions it booked stay as they are.
15. **Payees are stored by name, with aliases** (F09). Transactions, templates and series keep the payee's
    name, so the CSV files stay readable and the Transactions search, filter and export need no lookup.
    `payees.csv` keeps each name once (compared by `domain.payees.key`: case, accents and punctuation
    ignored) and its *aliases*. An import looks a bank text up by its `alias_key` first, then normalizes it
    and looks the result up by name or alias, and only then takes the normalized name. Importing remembers
    each text as an alias of the payee it was imported with (an alias belongs to one payee; changing the
    payee in the preview moves it). Renaming adds the old name as an alias and rewrites the name on every
    transaction, template and series; merging moves the others' aliases and names to the payee kept. Both
    are one undoable action. So after a rename or merge, the next import of the same text, or of another
    text that normalizes to a former name, lands on the payee the user kept. A name typed as a new payee
    takes over a former name of another payee. Lookups compare whole keys, never fuzzy matches.

16. **Categorization rules suggest, they never apply** (F07, decision 8). A rule is a text, a type, a category and
    an optional account; the longest matching text wins, then the newest rule. Its account is a suggestion like
    its category, not a condition. Saving a rule changes no stored transaction.
17. **Investments book their own transactions; their accounts are valued, not recorded** (F21,
    decision 9). Prices are typed in or pasted, never fetched. Trades and prices are the source:
    the transfers, fees, dividends and *Capital gains on investment* revaluations they imply are
    booked as ordinary transactions linked by `invest_id`, rebooked from all of a holding's trades
    and prices in date order after any change (so back-dated entries, edits and deletes stay exact),
    and changed only through them, like an exchange's spread (F44). A securities or crypto account's
    month-end balance is computed from the holdings rather than written as entries: nothing can fall
    out of step, undo needs nothing extra, and its reconciliation is 0 by construction. It takes no
    other transactions or balances; an account becomes one only while empty, so existing balances
    and manual revaluations are never reinterpreted; an account with history becomes one through a
    conversion (decision 13) that leaves its past as it was and starts from opening holdings. Recorded
    balances still drive the projection's start, so a price update doesn't shift it. Other classes
    (pillar 2, 3a, real estate, other) stay valued by recorded balances; the class only groups them.
18. **A dividend books gross income and its withholding tax as Taxes** (F21, decision 12). The
    alternative – booking only the net as income – was weighed and rejected: the gross is what a Swiss
    tax return declares and what a dividend budget usually plans, and the withholding tax is a real tax
    that is later credited against the income tax. With gross income and a tax expenditure, *Net income*
    and the reconciliation are unchanged (income − tax = what the account received); *Taxes* shows the
    withholding tax, so a month with dividends can look over a taxes budget and the projection's
    "higher of recorded and budget" may count it for that month. The reclaimed tax comes back as a lower
    tax bill (or a refund booked as income) the following year. Performance counts net dividends, the
    money actually received; the reclaimable sum is shown apart. Only holdings in CHF accounts count as
    reclaimable (a simplification: a foreign share held in CHF would carry foreign tax).
19. **Tags are stored by id; deleting one takes it off its records** (F10, decision 17). Transactions, templates and series keep tag ids, so renaming and recolouring change `tags.csv` only, and a name may be anything but `|`. Deleting a tag is not refused while it is used: tags are labels, so it is taken off every transaction, template and series in the same undoable action, and the confirmation says how many transactions lose it. Merging moves the others' records to the tag kept, which keeps its own name, colour and budget. A tag id left behind by an undo is ignored on display and dropped at the next save of its record. The German word is *Schlagwort* (*Tag* means day), French *étiquette*, Italian *etichetta*.
20. **Attachments are copied into the data folder; their files outlive their records while an undo
    can restore them** (F12, decision 16). A file goes to `attachments/<transaction id>/<attachment
    id>.<ext>` (written whole, then renamed) before its record is added, so a record never names a
    missing file. Removing an attachment, or deleting its transaction, deletes only the record, as
    an ordinary undoable change; undo needs nothing extra. `attachments.cleanup` at startup deletes
    the files that no record and no change in `history.jsonl` (the newest 500 actions) names, plus
    interrupted writes and empty folders, and leaves files of other names alone. A trash folder was
    the alternative, but undo would then have to move files, which the generic history cannot.
    Paths are always built from the record's ids and accepted only when both are plain names
    (letters, digits, `_`, `-`) and the result stays inside `attachments/`; the user's file name is
    only shown. Accepted types are checked by extension and by their first bytes (PDF, JPEG, PNG,
    WebP, HEIC, the ZIP of Office and OpenDocument files, text without NUL bytes in UTF-8 or
    Windows-1252); HTML, SVG and XML are refused, since the app shows attachments itself. The same
    check (`domain.attachments.stored_type`: an accepted extension in the stored name, and content
    that matches it) guards every place that trusts a stored file, because the system viewer picks
    its program by the extension and would run a script: `services.attachments.verified` before
    *Open*, *Save a Copy…* and serving, `system_open` itself again, and `archive.check` for every
    attachment file and record of a backup, which is refused as a whole otherwise. A file that got
    into the folder by hand or from a crafted backup is therefore never opened, served or copied.
    The route `/attachment/{id}` looks the id up among the records (anything else, and a file that
    fails the check, is 404), serves
    the content type of the stored file's extension, not the record's text, with `nosniff`, inline
    or, with `?download=1`, as a download. The app has no login and listens on localhost only
    (decision 9), so the route adds no access check of its own. In the app window *Open* hands the
    file to the system viewer instead; *Save a Copy…* goes through `save_file`. Backups are built and
    read in memory, so they are bounded at 2 GB; `create` refuses a larger one rather than writing a
    backup that can't be restored.
21. **A split is a category per item line; the parts always add up to the transaction** (F11, decision 14 of the
    catalog). The receipt lines already existed, so a split needs no second list: a line of an expenditure may
    name a category and tags, and every sum by category or tag counts `domain.splits.parts` instead of the
    transaction. Alternatives weighed: split rows as separate transactions (they would break the one-payment,
    one-row model, the import fingerprints and undo of a single payment) and a separate split table (a second
    list beside the items, with the same lines typed twice). The parts are computed in whole cents of the
    original currency and micro-CHF, each line at the transaction's own rate, with the rest – or, when nothing
    is left of it, the largest line – taking the rounding, so CHF totals equal `amount_chf` exactly and no
    category shows a stray cent. Lines may not exceed the amount, but a negative line is allowed: it is a
    discount (or a deposit returned) on that category, which may end up negative for the transaction, like a
    refund. An advance is shared proportionally by every part rather than taken from the rest or from one line:
    it needs no choice in the form, a receipt shared half and half stays half and half in every category, and
    the advance share itself, its repayments and their settlement are untouched. Income, transfers, exchanges
    and revaluations are never split; their lines lose a category when saved. A line whose category equals the
    transaction's is stored without one, so a later change of the transaction's category (bulk edit) moves it
    along; bulk edit only ever changes that own category. Making a split transaction recurring copies no items
    (series have none), so its bookings are not split.
22. **A QR bill is a record of its own; its file is an attachment of the bill that the payment takes over
    without moving** (F03, decision 15). Attachments belonged to transactions, but a scanned bill has none until
    it is paid. Of the options – files of their own for bills, a pending list in memory, or attachments with a
    second owner – the last keeps one table, one route, one content check and one backup path: an attachment
    has a `bill_id` as well as a `transaction_id`, and its folder is the bill's (`Attachment.folder`:
    `bill_id or transaction_id`). Paying the bill fills in `transaction_id` on the same record, so the file is
    listed with the payment, served and backed up as before, and never moves: undoing the payment, like any
    undo, only restores records (decision 20 relies on that). Deleting the payment hands the file back to the
    bill and opens the bill again (`attachments.remove_for`, `bills.release`); deleting an open bill removes
    its file record (the file stays while an undo can bring it back), deleting a paid one keeps the payment's
    file. `ids_ok` requires plain ids for both owners and the stored name, so the path checks, `cleanup`
    (which now keys files by folder) and `archive.check` cover bill files exactly as transaction files. A bill
    is a duplicate when IBAN, reference and amount match a bill in the list, whatever its status; bills
    without a reference also compare message and bill information, or every monthly bill of the same amount
    would be refused. Payment goes through the ordinary transaction sheet (or `transactions.save` in
    `mark_paid`), so validation, valuation, payees (`payees.resolve` of the creditor, then `spelling` and
    `ensure`) and the F07 suggestion are those of any expenditure. The payload is read as strictly as a bank
    reads it (rejected codes name their fault) except where the meaning is unambiguous: spaces in IBAN or
    reference, a final line break, fewer than two decimals. Decoding is local (decision 1 of the catalog's
    cross-cutting decisions: no online services); PDFium renders vector and embedded QR codes alike, which
    image extraction alone would miss.

## 8. Testing

- `uv run pytest` runs the unit tests (`tests/`) for:
  - FX parsing, cross-rates and date fallback;
  - COICOP level counts and roll-up;
  - every transaction type, the advance split, settlement and deletion rules;
  - recurring transactions: month-end days, missed months, end dates, booking in order,
    skipping, automatic booking only once, edits that change future occurrences only, and
    undoing a booking;
  - budget expansion, overwrite, horizon extension and shrinking, and no resurrection of
    deleted or overwritten months;
  - CSV round-trip, corrupt-file reporting and formula-safe exports;
  - CSV import: detection of encoding, separator, header line, columns, number and date
    formats; duplicates on re-import; booking-date valuation; saved profiles;
  - camt.053 / camt.054 import: versions .001.02/.04/.08 and unknown namespaces, batch entries,
    pending entries, bank and content references, refused DTDs and non-camt XML, the IBAN memory
    and the currency check;
  - month-end balances from bank files: newest- and oldest-first balance columns, debit/credit
    columns, partial last months, a month ending on its last day, inconsistent balances, camt
    closing balances and replacing a recorded balance;
  - payees: 47 real bank lines (German, French, Italian, English) normalized, alias keys that ignore dates
    and numbers, lookup order, rename and merge in one undo step with the next import landing on the kept
    payee, deleting unused payees only, old files without payee columns, CSV and camt imports;
  - translation completeness and language switching;
  - edit valuation and locking of repaid advances;
  - bulk edit: each field only on the rows it applies to, note modes, all or nothing with the
    failing rows named, one undo step, exchanges with their spread, advances and repayments, and
    booked recurring rows;
  - tags (F10): new names written only when stored (and undone with their transaction), ids that no longer exist dropped, templates, series and bookings, rename, recolour, merge and delete across records in one undo step, totals (own share, repayments left out, period), budgets with open ends and another currency, bulk add and remove, old files without the column, `tags.csv` and the JSON export;
  - category suggestions: folded matching in payee or text, longest text and then newest rule first, history by
    payee with ties to the latest, the text fallback for rows without payee, accounts only where none is fixed,
    rules with deleted categories skipped, nothing written, 4'000 transactions; rules saved, replaced, deleted and
    undone; CSV and camt imports with suggestions, clearing them and keeping them on import;
  - attachments (F12): every accepted type and the refused ones (empty, over 20 MB, wrong content
    for the extension, HTML, SVG, executables), names stripped of paths, files stored under
    generated names, remove and transaction delete undone with their file, cleanup only once no
    undo can bring a file back, backup round trip with files, restore from the before-restore and
    update copies (hard links), refused archive paths, sizes and damaged members, unsafe records,
    backups with a `.command`, `.sh` or a script named `.pdf` refused, *Open* refusing such files
    planted in the folder, and the route (known records only, traversal attempts, forged content
    types, mismatched content, missing files);
  - QR bills (F03): the guidelines' QR-reference and creditor-reference examples, a bill without amount,
    debtor or reference, combined (K) addresses in EUR from Liechtenstein, payloads written by the `qrbill`
    package (S and K), IBAN, mod 10 and ISO 11649 check digits, 37 refused payloads (version, coding, line
    count, IBAN, address types and lengths, reserved fields, amount format and range, currency, reference
    rules, trailer, length limits), Swico bill information with escapes and due dates; QR codes read from
    vector and picture PDFs, several pages and bills, PNG, rotated JPEG and WebP files generated in the tests
    (`tests/qr_fixtures.py`: segno, reportlab, Pillow), unreadable and refused files; scanning, duplicates,
    paying with the suggested or a chosen category and amount, the file moving with the payment and back,
    one undo step each, dismiss, reopen, delete, cleanup, backup and restore of bill files, unsafe ids in
    a backup, the JSON export and the file route; the Bills page at work in the simulated client;
  - wealth with carry-forward, projected wealth (higher of recorded and budget) and the reconciliation;
  - split transactions (F11): the parts adding up exactly in cents and micro-CHF (also 400 random receipts with
    discounts, tags and advances), foreign currencies at the transaction's rate, the CHF rounding never left on
    an empty rest, discounts, lines above the amount refused, line categories kept only on expenditure, old
    item files without the columns, the statement, budget vs. actual, the projection, chart series, the
    COICOP page and tag totals counting the parts, a split never changing any month's net, balances,
    unexplained difference or projection (CHF, EUR, USD views), advances shared proportionally with repayment,
    settlement and the edit lock unchanged, tags deleted and merged on lines, bulk edit changing only the rest,
    and undo of a transaction with its lines;
  - investments (F21): the booked value staying at quantity × price after buys, sales between two
    prices, back-dated trades and prices, edits, moves and deletes (also 40 random events with
    random edits and deletes), oversold sales refused, fees, dividends and their category, one undo
    step, performance with realized and unrealized, asset classes and old files without the column,
    refused manual bookings on investment accounts, price lists in several formats, and a month with
    trades and price updates reconciling to 0 (CHF and EUR view, and a USD depot viewed in CHF).
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
