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
- **Receipts (F04):** RapidOCR (`rapidocr`, with its PP-OCRv6 models in the wheel) on ONNX Runtime reads till
  receipts, when the optional extra `budget-app[ocr]` is installed (it is in the installers); on the computer,
  nothing is downloaded or sent. The app works without it (the receipt is then typed from its picture).
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
| `main.py` | CLI entry (`uv run budget`). `register_pages` puts every page behind the app lock's guard (F27) and adds the lock page; `register_routes` adds the attachment, unlock and notes routes; `open_folder` closes the open folder and opens another (the profile switch, F34: folder lock first, then the folder's own lock state and store); `open_data` opens the folder (at startup, or after the unlock when it is encrypted); `refresh` loads ECB rates at startup and every 30 min, then books what automatic recurring transactions have due, then runs the update check if the user turned it on (F33). |
| `config.py` | Base currency `CHF`, ECB URL, the GitHub repository and its releases URLs (F33), data/resource paths. `home_dir()` is the app's home folder (`BUDGET_DATA_DIR` / `--data-dir`, the OS user data folder for installed builds, `./data` otherwise); `data_dir()` is the folder to open at startup, the current profile's (F34, from `storage.profiles`): the one place that decides it. |
| `domain/months.py` | `YYYY-MM` month arithmetic and the "month complete" rule. |
| `domain/fx.py` | `FxRates` (parse ECB CSV, rate lookup with date fallback, cross-rate conversion, `history()` for the rate chart) and `FxService` (daily download, atomic file replacement). |
| `domain/coicop.py` | COICOP tree (ancestors, children, labels) plus pseudo-categories `TAX` and `ADV`; `fold()` makes text case- and accent-insensitive for searching. F40: `notes(code, lang)` → `Notes` (tuples of items for `includes`, `also_includes`, `excludes`, and `translated`) or None, read on first use and cached; a language whose file is missing or marked `_untranslated` gets English, and so does a text the translation lacks. `note_words(code, lang)`: the folded words of includes and also includes, in the language and in English, each once (excludes is never searched). `match_words(query, primary, secondary)`: whether every word occurs in either, and whether in the first. `notes_payload(lang)` / `notes_version(lang)`: all notes for the browser and a short hash of them. |
| `domain/everyday.py` | F08. Everyday names and search words of the common categories (`resources/everyday_categories.json`) and the category picker's options: `category_options(recent)` lists the recently used codes first, then COICOP order with Taxes last; each option has its label (`Groceries · 01.1`, or the COICOP label for the others), the official name as caption, its level, a folded `search` text (code, everyday names in all four languages, official name in the interface language and in English, search words) and `note_search`, the note words (F40). `matches()`: every query word must occur in `search` or `note_search`; `search()` lists the options that match by `search` alone first, then those that need the note words. |
| `domain/payees.py` | F09. Payee names from bank texts, deterministic and offline: `clean_words` drops dates, times, card, reference, terminal and phone numbers, IBANs and postal codes, the booking words around the merchant (UBS, PostFinance, ZKB, Raiffeisen, Revolut, TWINT; German, French, Italian, English: "Einkauf", "Kauf/Dienstleistung vom", "Paiement à", "Card payment to" …) and payment services in front of it (`TWINT *`, `SumUp *`, `PAYPAL *`); `normalize` names a brand from `resources/payees.json` (longest pattern first; banks and payment services only when nothing else matches) or keeps the rest without the location (a legal form at the end stays, spelled AG, GmbH, SA, Sàrl, Sagl …; decision 11), title-cased when the bank wrote capitals (acronyms such as SBB stay); `alias_key` is the folded cleaned text (location kept) a bank text is remembered by; `key` folds a name for comparing (case, accents and punctuation ignored). |
| `domain/tags.py` | F10. The tags column of transactions, templates and series: tag ids separated by `\|` (`split`, `join`), `clean_name` (single spaces, no `\|`) and `key` (case, accents and spacing ignored); `PENDING` marks a name that becomes a tag only when its record is stored. |
| `domain/splits.py` | F11, pure. The one place that decides how an expenditure's amount is counted per category (§4.3, decision 21): `Line` (an item line's total, category and tags), `Part` (category, amount, amount_chf, tags), `is_split` (a category other than the transaction's, or tags of its own), `parts(amount, amount_chf, category, lines, advance, tags)` (whole cents and micro-CHF, so the parts add up exactly), `split_total`, `rest` and `overflow`. |
| `domain/proposal.py` | F16, pure. Budget from history (§4.4, decision 24): `basis_months(current, n, first)` (the n complete months before the current one, without those before the first record), `at_level(code, level)` / `by_level` (a code counts in its ancestor at the chosen COICOP level; codes not deeper, *Taxes* and unknown codes stay), `statistic(values, method)` (`AVERAGE`, `MEDIAN`), `round_to(value, step)` (half up, in `Decimal`), `propose` (rounded, never below 0), `has_activity`; the choices `BASES`, `LEVELS`, `STEPS`. |
| `domain/trends.py` | F18, pure. Every threshold of *Notable this month* in one place (`HISTORY_MONTHS`, `MIN_HISTORY_MONTHS`, `ABOVE_USUAL_RATIO`, `ABOVE_USUAL_MIN_CHF`, `LARGE_MIN_CHF`, `LARGE_MIN_SAMPLES`, `LARGE_TIMES_USUAL`, `LARGE_SHARE_OF_MONTH`, `OVER_BUDGET_MIN_CHF`, `PACE_MIN_DAY`, `PACE_MIN_PAYMENTS`, `MAX_ITEMS`; §7, decision 26) and the rules on them: `median`, `above_usual`, `is_large`, `exceeded`, `pace`, `about_to_exceed`; `Change` (actual against a reference: `delta`, `ratio`, `empty`) and `percent` for the Budget page. |
| `domain/rules.py` | F07. Which categorization rule applies: `match_key` (folded text, single spaces), `by_priority` (longest text first, then the newest), `first_match` / `best` (the first rule of the type whose text occurs in the payee or the bank text / note). |
| `domain/models.py` | Dataclasses `Account`, `IncomeCategory`, `Transaction`, `AccountEntry`, `BudgetEntry`, `Recurring`, `Payee`, `Rule`, `Tag` (with the palette `TAG_COLOURS`), `Holding`, `Trade`, `Price`, `Attachment` (with its `folder`), `Bill` (F03), `TaxSection` (F20), `Loan` (F22), `Settings`; the asset classes (`ASSET_CLASSES`, `HOLDING_CLASSES`, `ASSET_LOAN` with `Account.is_loan`) and trade kinds. |
| `domain/investments.py` | F21, pure. A holding's trades and prices as `Event`s in the order they count (`events`: by date, a day's opening holding first, then its trades in entry order, then its typed or pasted price), `replay` (per event the quantity before and after, the previous and new price, the money moved and the revaluation, and the booked value round(quantity × price, 2)), `oversold`, `value_on`, `price_date`, `performance` (`Performance`: value, opening value, bought, sold, net dividends, withholding tax, fees, average cost, realized; money put in, gain, %, unrealized; `scaled` and `+` for totals), `default_withholding` (35 % in CHF), and the price-list helpers `date_format`, `parse_day`, `parse_number`. |
| `domain/review.py` | F19, pure. `Period` (year, first and last month, `complete`, `months`, `before`, `previous()`: the same months a year earlier) and `period(year, today)` (January to December for a past year, January to the current month for this one, no period for a later year), `savings_rate` (None without income), `share`, `TOP_EXPENSES`. |
| `domain/subscriptions.py` | F17, pure. The rules that make charges a subscription (§7, decision 33): `PERIODS` (1, 2, 3, 6, 12 months), `period_of_gap(days)` (within `GAP_TOLERANCE_DAYS` + one per month), `latest_run(days, amounts)` (going back from the last charge while each gap fits the last gap's period and the amounts stay similar: a skipped or late charge, an irregular one or an old price ends the run), `enough(period, count)` (`MIN_CHARGES` 3, `MIN_CHARGES_LONG` 2 from `LONG_PERIOD` 6), `similar(amounts)` (within `AMOUNT_TOLERANCE` 20 % or `AMOUNT_TOLERANCE_MIN` 2.00 of the median), `active(last, period, today)` (no older than `STALE_PERIODS` 2 rhythms), `next_charge`, `detect(charges, today)` → `Rhythm` of the latest run (period, median amount, first, last, count, `yearly`); charges on one day are one charge. |
| `domain/loans.py` | F22, pure. `interest(debt, rate, months)` (simple interest for one payment period, debt × rate % × months ÷ 12, half up to the cent in `Decimal`), `occurrence` (payment dates with the month-end rule of §4.5), `schedule(debt, rate, months, amortization, first, limit, until)` → `Payment`s (date, interest, amortization, debt after; the last takes what is left; an interest-only loan stops at `limit` or `until`), `payoff`, `interest_total`, `repaid_share`; `INTEREST_CATEGORY` (12.2.1). |
| `domain/goals.py` | F14, pure. `whole_months(today, due)` (one less when the due day comes before today's, at least 1 until the date has passed), `monthly_needed` (rounded up to the cent), `expected` (the straight line from start to target), `progress(current, target, today, due, start, start_amount)` → `Progress` with a status (`reached`, `overdue`, `on_track`, `behind`, `no_date`, `no_target`), the months left, the monthly amount needed and `percent` (never 100 before reached), and `planned(progress, n)`: what a goal is to hold `n` months from now (decision 23). |
| `domain/attachments.py` | F12, pure. The accepted file types (`TYPES`: extension, content type, preview kind and a check of the first bytes; `file_type` needs both to agree), `display_name` (the last path part, no control characters, at most 200 characters), `stored_name` / `is_stored_name` / `is_id` (plain names only), `MAX_BYTES` (20 MB). |
| `domain/tax.py` | F20, pure. The sections of the tax summary (`SECTIONS`, `Section`: key, title, note, COICOP `codes` counted with their subcategories, `exact` codes counted alone, a `rule` for pillar 3a, the withholding tax and the securities list, `income` for the section where income counts positive), `covers(code)`, `section_of_code` (at most one: the code lists don't overlap, tested) and `INCOME_NAMES` (Dividends, Interest, Interest income: income categories by name in any language). See §7, decision 25. |
| `domain/receipts.py` | F04, pure. `TextBox` (a text the engine read, with its top-left corner and height), `rows(boxes)` (boxes within half a height of a row's first box form one row, joined left to right), `amounts` (`11.45`, `1'234.50`, `1 234,50`, trailing or leading minus; never a date or a time), `find_date` (day first, two- or four-digit year, ISO, also glued to a time; not in the future, not before 2000), `find_currency` (CHF, Fr., SFr., EUR, €), `parse(rows, today, default_currency)` → `Receipt` (merchant, date, total, currency, `ReceiptLine`s, rows; `empty`): the total from the last row naming one (`TOTAL_WORDS`, de/fr/it/en) that is not a subtotal, tax, change or payment row (`NOT_TOTAL_WORDS`), else the largest amount; the merchant the first row of words before the total that is not a date, amount, phone number or bookkeeping (`NOT_MERCHANT_WORDS`); the lines between them that end in an amount, only when they add up to the total. See §7, decision 29. |
| `domain/qrbill.py` | F03, pure. The Swiss QR-bill payload (SIX Implementation Guidelines 2.3, version `0200`): `parse(text)` → `QrBill` (IBAN, creditor and debtor `Address` of type S or K, amount as `Decimal` or None, currency, reference type and reference, message, bill information, alternative procedures) or `QrBillError` (an `N_` message with its values, `translated()`); the checks `iban_valid` (CH/LI, mod 97), `is_qr_iban` (institution id 30000–31999), `qr_reference_valid` / `mod10_check_digit` (recursive mod 10), `creditor_reference_valid` (ISO 11649); `is_payload`, `format_iban`, `format_reference`; `swico(bill_info)` (Swico S1: invoice number and date, customer reference, VAT number, payment terms; `\/` escapes) and `due_date` (invoice date + the 0 % term). See §7, decision 22. |
| `storage/csv_store.py` | Generic `CsvTable[T]`: in-memory records, atomic rewrite (temp file, fsync, rename) on every change. Unknown columns are kept and written back. |
| `storage/store.py` | `Store` bundling all tables and settings. Unknown settings keys are kept. `reload()` reads every file again into the same object (F26). |
| `services/attachments.py` | Attachments (F12): `check` (empty, over 20 MB, type), `add` (file first, then the record), `add_to_bill` (a QR bill's file, F03), `remove`, `remove_for` (with a deleted transaction; a bill's file goes back to its bill instead), `remove_for_bills`, each one undoable action; `list_for_bill`, `ids_ok` (plain ids for the folder and the file), `list_for`, `counts` and `names_by_transaction` (the Transactions list), `path` (built from plain ids, inside `attachments/` or None), `read`, `exists`, `kind`, `size_label`, `system_open` (`open`, `xdg-open` or `os.startfile`) and `cleanup` (files no record and no undo can bring back; at startup). |
| `services/items.py` | Product lists of transactions: validation (blank lines ignored, half-filled lines rejected, a line's category must be a COICOP code or `TAX`), `check_split` (lines with their own category or tags may not exceed the amount, F11), replace (a line keeps its category and tags only on an expenditure; new tag names become tags), totals, counts and names for the Transactions list. |
| `services/qrcodes.py` | F03. `texts(name, data)`: the QR codes of a PDF (every page up to 20, rendered grayscale at scale 3, about 216 dpi, then once more at scale 6 when none was found) or a photo (JPEG, PNG, WebP; EXIF orientation), in page order, each once, decoded as UTF-8; HEIC and other types are refused with a message. The libraries are imported when a file is read, not at startup. |
| `services/ocr.py` | F04. The engine boundary: `available()` (both packages importable and the three model files present, nothing loaded; cached), `recognizer()` (RapidOCR loaded once, under a lock, logged and remembered when loading fails) as a `Recognizer`, a function from a Pillow image to `TextBox`es; `boxes` converts RapidOCR's polygons and texts. Tests inject their own recognizer; the engine never runs in tests. |
| `services/receipts.py` | F04. `prepare(name, data)` → `Prepared` (the file checked like an attachment, PDF or photo only; the pictures for the engine, the first three pages of a PDF at about 216 dpi in RGB, a photo turned upright; the `preview` at most 1200 px; a PDF's own text rows when they hold an amount), in the event loop; `pdf_text`; `read(prepared, recognizer, today, default_currency)` → `Receipt` or None without any engine, raising `ValidationError` when the engine fails, meant for a worker thread; `prefill(ctx, receipt, today)` → the `TransactionInput` (date, amount, currency, the merchant resolved through `payees.resolve`) and the `ItemInput`s. |
| `services/bills.py` | F03 (decision 15). `read` (the valid bills of a file and the reasons for refused codes; a notice of 0.00 is refused), `scan` (adds new bills with the file as attachment; refuses a file without a usable or new bill), `from_payload`, `bill_key` (duplicates), `open_bills` (by due date, undated last), `closed_bills`, `due` / `due_count` (overdue or due within 7 days, the sidebar badge), `payee` (`payees.resolve` of the creditor), `note`, `payment_input` (the sheet's prefill), `suggestion` (F07), `mark_paid`, `check_payment` / `paid_with` (the sheet's way), `release` (a deleted payment opens its bill again), `dismiss`, `reopen`, `set_due`, `delete`, `payment`; every write one undoable action. |
| `storage/backup.py` | On the first start of a new app version, copies the data files to `<data>/backups/` before the store loads them, and hard-links the attachment files there (copies where links fail; they never change once written). The version that last opened the folder is in `<data>/.app_version`. |
| `storage/lock.py` | Single-instance lock on `<data>/.lock`, so two processes cannot overwrite each other's files; `switch(folder)` locks another folder first and lets go of the old one only then (F34, F26). |
| `storage/claim.py` | F26. Which computers have a folder open: `.in-use.<host>.json` per computer (`write` as the heartbeat, `release`, `others` = other computers' claims seen within `STALE`, 10 minutes); not a record file. |
| `storage/watch.py` | F26. The size and change time of every record file as this app last read or wrote it (`note`, called by `encryption.read_text` / `write_text`; `baseline`); `changed(paths)` = files someone else wrote. |
| `storage/profiles.py` | F34. The profile registry `profiles.json` in the home folder: `Profile` (name, absolute folder, id), `Registry` (`current`, `find`, `active`), `load` (a missing or damaged registry is the single profile *Budget* in the home folder), `save` (temp file, fsync, rename), `current_dir(home)`. Not a record file: never backed up, restored or encrypted. |
| `storage/encryption.py` | F27. `Codec`: the record files' form on disk, plain text or (with a Fernet key) a marker line and a Fernet token; `read_text` / `write_text` (whole file, fsync, rename) used by every table, the settings, the history and the import memory files; `reencode(folder, codec, write)` rewrites a folder's record files in another form. A codec with a key reads plain files too. |
| `storage/vault.py` | F27. `Vault`: `<data>/security.json` – the password's salt, scrypt parameters and verifier; while encryption is on, the data key wrapped with the password's and with the recovery key's stretched key; the phone-companion flag (F35). `set_password`, `verify`, `change_password`, `remove_password`, `enable_encryption` (returns the data key and the recovery key), `unlock`, `unlock_with_recovery`, `reset_password`, `new_recovery_key`, `disable_encryption`. Primitives from `cryptography` only. |
| `storage/history.py` | Change history and undo (F28): every table write records the record before and after (as written to the CSV file); changes made together form one *action* (`history.action(label)`, nested ones join the outer). Undo reverts an action in reverse order after checking every record still looks as the action left it. Kept in `history.jsonl` (the newest 500 actions), not part of backups. |
| `services/context.py` | `Context` (store + FX), conversion helpers, budget horizon, `ValidationError`. `init_context(data_dir, codec)` opens a folder (also after the unlock of an encrypted one); `is_open()`, `close_context()`. |
| `services/security.py` | F27, F35. The process-wide lock state: the open folder's `Vault`, its `Codec` (the key once unlocked) and the sessions, random tokens in the `budget_session` cookie, kind `desktop` (password) or `phone` (pairing code), kept in memory only. `allowed` / `redirect_for` for the page guard; `unlock` and `recover` (wrong answers slowed from the fourth on), handed over as one-time nonces that the `/unlock/<nonce>` route turns into the cookie; `lock`; the Settings actions (`set_password`, `change_password`, `remove_password`, `enable_encryption`, `disable_encryption`, `new_recovery_key`), which re-encode the data folder and its `backups/` copies; `opener` opens the store after the unlock of an encrypted folder. |
| `services/profiles.py` | F34. The profile rules: `add` (name unique ignoring case; folder typed or `<home>/profiles/<name>`, never another profile's, the home folder, a folder directly inside it or a file), `rename`, `remove` (forgets; not the open one), `switch` (opens the folder through `opener`, set by `main.open_folder`, before the registry changes); `move` (F26: copies a folder, without the lock, claim, registry and temp files, to a new or empty one and reopens the open profile there as the same records). |
| `services/sync.py` | F26, decision 19. `start(folder)` claims the open folder and takes its files as seen, `stop()` releases it, `others()` lists other computers with it open, `tick()` (every `INTERVAL`, 5 s, from `main.check_folder`) refreshes the claim once a minute and reloads the store (`Store.reload`, in place) when record files changed elsewhere and are the same on two checks; `cloud_folders()` for the Move sheet. |
| `services/phone.py` | F35, decision 21. `Gate` (ASGI middleware on every server: from another device only `/pair`, `/quick`, the unlock route and NiceGUI's files and socket, only from a private address, only while phones are allowed; else 403 or a closed socket), `admitted(host, path)`, the home-network listener (`start` / `stop` / `sync_listener`: a second uvicorn server on the same app, all interfaces, port 8484 or a free one, inside the app's loop), `addresses()` / `urls()` (the computer's private IPv4 addresses) and `qr_data_url` (zxing-cpp). |
| `services/accounts.py` | Accounts (with their asset class, F21; `add_loan_account` for F22, the class `loan` is refused otherwise and kept while the loan exists), advance-payment accounts and income categories, with their deletion rules. |
| `services/transactions.py` | Validation and saving of the four transaction types, advance payments and settlement; deleting releases the QR bills a deleted transaction paid (F03); `recent_categories()` (the last ten distinct expenditure categories, for the category picker). An edit is `prepare_edit` (validate and build the new record, nothing saved) then `store_edit` (save it, keeping an exchange's spread booking in step); `save` does both. |
| `services/bulk.py` | Bulk edit (F13): `BulkChanges` (expenditure category, income category, payee, account, tags to add and to remove (F10), note mode and text; `None` or an empty list leaves a field as it is), which field applies to which row (`takes_*`, `is_spread`), `plan()` (every selected row as form input with the changes applied, checked with `prepare_edit`; rows the changes leave as they are drop out, also when the built record equals the stored one) and `bulk_edit()`, all or nothing (`BulkEditError` names the failing rows by date and amount) as one recorded action. Adding a field means one attribute, one `takes_*` rule and one line in `apply()`. |
| `services/budget.py` | Budget series expansion, overwrite, horizon change and "Forever" extension. |
| `services/budget_history.py` | Budget from history (F16, §4.4): `Options` (basis, method, level, rounding, period), `first_record`, `history` (CHF per category and basis month, one pass over the transactions with `aggregates.expenditure_parts`), `propose` → `Proposal` (basis, first month, target months or the period's error, `Row`s with history, proposal, `existing` (`Budgeted`: months and monthly range of the same category's budget in the target months), `overlaps` (budgets of categories inside or above it) and `recurring` (running series booking in it)); `create(ctx, opts, choices, comment)`, all or nothing in one recorded action, a choice with `replace` using `budget.create`'s overwrite; `note`, `category_label`. Writes nothing but `create`. |
| `services/recurring.py` | Recurring transactions (F05): series, their schedule (`occurrence`, `next_after`), the due occurrences, `book` / `skip` / `book_all` through `transactions.save`, and `book_automatic` for series that book without asking. |
| `services/wealth.py` | `revalue_entries` values every account entry at its month-end rate after each rate check (decision 36). Account balances (a securities or crypto account's from its holdings, F21; a loan account's minus its debt, F22), wealth, wealth by asset class (`by_class`), projected wealth, wealth time series; `projected_wealth` and `wealth_series` take a `net` function (`NetFn`), the plan's `projected_net` by default or a scenario's (F15). |
| `services/investments.py` | F21. Holdings (add, rename, delete with everything they booked), trades (`TradeInput`, `validate_trade`, `save_trade`, `delete_trade`) and prices (`save_price`, one per holding and day; `delete_price`; `price_effect` previews one), price lists (`read_price_list` reads, `save_prices` saves), each one undoable action. `_book` books a holding's linked transactions again from all its trades and prices after every change (§4.6). `value_entry` is a securities or crypto account's month-end balance, `account_value`, `performance`, `history`, `source` (the trade or price of a booked row, or the converted account), `reclaimable_tax` (withholding tax on CHF dividends per year). Conversions (decision 13): `needs_conversion`, `check_conversion` (rules and preview), `convert_account` (one undoable action), `book_conversion` (the opening difference as a linked revaluation). |
| `services/loans.py` | F22. Loans (`LoanInput`, `validate`, `add` with its liability account through `accounts.add_loan_account`, `edit` with `planned_next_due` like a recurring series, `delete` with the account once nothing uses it), the **debt** (`debt(ctx, loan, on)`: principal + what left the account − what came in, up to `on`), `value_entry` (a loan account's month-end balance, minus the debt; used by `wealth.balance_entry`), `first_month`, `status` (`LoanStatus`: debt, the next `PREVIEW` planned payments, payoff, interest this year, share repaid), `renewals` (fixed terms ending within 90 days), `payments` and `interest_paid` (the rows with `loan_id`), and the due payments (`due` → `DuePayment` with the interest on the debt before each due date, `due_count` for the sidebar badge, `book` (a transfer and an interest expenditure through `transactions.save`, amounts and date editable, refused above the debt), `skip`, `book_all`), each one undoable action. |
| `services/shared.py` | F23. Who owes what, read from the advance payments (§3): `people(ctx, today)` → `Person`s (name as last written, `key` = the debtor folded with `coicop.fold`, the open `OpenAdvance` items oldest first, `open` and `settled_year` per currency, `oldest`, `open_in(currency)`; one pass over the transactions, a repayment counting for the debtor of the advance it settles), `person(ctx, key)`, `item_label`; settling up: `plan(person, currency, amount)` → `Settlement` (the open items in that currency oldest first, the last one partly; refused above what is open) and `settle(...)`, one repayment per item through `transactions.save` as one recorded action *Settle up*; the summary: `summary_lines` / `summary_text`, `totals_lines`, `summary_pdf` (ReportLab, one table), `file_name`. Writes nothing but `settle`. |
| `services/aggregates.py` | Sums of transactions and budget entries per category and month; `projected_net` (the higher of recorded and budget per category; `net_with_budgets` does the same with budgets given, for a scenario, F15). Shared by reports and the wealth projection. F11: `split_lines` (the item lines with a category or tags, per transaction, read once per sum), `transaction_parts` (a stored transaction's `domain.splits` parts; only expenditure is split), `part_value` (a part in the view currency, converted like `tx_value`), `expenditure_parts` and `is_split`. Every sum by category or tag goes through `transaction_parts`. |
| `services/reports.py` | Monthly statement (expenditure tree, income, budget vs. actual, reconciliation, opening balances), chart series; `income_names`. |
| `services/trends.py` | F18. `reference_month` and `compare(ctx, rep, mode)`: the month before or a year before as a `Comparison` (rolled-up expenditure per code and income per key, by part like the statement; `change(line)` gives a `Change`), adding rows for what only that month had. `notable(ctx, view_cur, today)` → `Notable` (the month, up to five `Item`s of kind `over_budget`, `large`, `above_usual`, `pace`, the number of history months, whether anything is recorded): one pass over the expenditure of the current month and the six before, each payment's parts (F11, advance share left out) summed per COICOP group (`group_of`); thresholds from `domain.trends`, converted from CHF at the latest rate. |
| `services/review.py` | F19. `years` (the years with a transaction or a balance, and this one), `default_year` (last year when it has data), `review(ctx, year, view_cur, today)` → `YearReview`: income and expenditure as `Figure`s (this year, the year before, `change` as F18's `Change`), `expenditure_lines` (the statement's top-level rows with a value in either year), `income_lines` (per category and the advance repayments), `biggest` (`BigExpense`: the five biggest payments by own share), `monthly` (`MonthFlow` per month), wealth at the start and at the end of the last complete month with the year before's change; `saved`, `savings_rate`, `has_data`. The sums are `aggregates` over the period's months, per part (F11). |
| `services/review_export.py` | F19. `frames(review)` (Summary, Months, Expenditure, Income, Biggest Expenses in the interface language), `xlsx`, `pdf(review, chart_png, today)` (the summary table, the chart the page rendered when given, then the tables; footer with page numbers), `period_label` ("2026 so far (January to October)"), `comparison_label`, `percent`, `change_text`, `file_name`. |
| `services/subscriptions.py` | F17. `groups` (expenditure not booked by a series, per payee key or `note:` + folded note, and currency), `already_recurring` (groups a running expenditure series covers), `ignored`, `detect(ctx, view_cur, today)` → `Subscription`s from each group's latest run (name as last written, currency, period, usual amount, first, last, count, the run's most frequent category and account, its transaction ids, `yearly`, `yearly_view` at the latest rate, `next`), most expensive year first; `recurring_spend` (`SeriesSpend`: the running expenditure series with their yearly cost); `to_recurring(ctx, sub)` (F05's `RecurringInput` from the last charge at the detected rhythm); `ignore` / `unignore` (one undo step each). |
| `services/export.py` | Statement as Excel/PDF; chart data and list views as CSV/Excel; the PDF pieces the tax summary and the year in review share (`pdf_styles`, `para`, `PDF_TABLE_STYLE`, `money`); every record as one JSON document (`format: budget-app/export`, `version: 1`), not meant to be imported back. |
| `services/archive.py` | Full backup of the data folder's records and attachment files as a ZIP (with a `budget-backup.json` manifest; version 2 when it holds attachments, else 1) and restoring one: plain CSV/JSON names and `attachments/<id>/<id>.<ext>` paths only, at most 100 record files, 20,000 attachments of 20 MB each and 2 GB in all, every table must load, every attachment record must name plain ids with an accepted extension and every attachment file must pass the content check before anything changes; the current files go to `backups/<timestamp>_before-restore/` first (the attachments moved there), then the store is reloaded and the attachment files its records name are written. `create` refuses a backup `read` would refuse. |
| `services/undo.py` | `@recorded(label)` names a service function's action; `undo()` with readable errors; labels and one-line change descriptions for the history page (holdings, trades and prices included). |
| `services/updates.py` | F33. `parse_version` / `is_newer` (`v0.2.0` against `app_version()`; a dev build is never behind), `fetch_latest` (GitHub's *latest release* API through httpx, 1 MB cap, only a `tag_name` of the form `v1.2.3` accepted; `UpdateError` otherwise), `release_url` (built from the checked tag), `check` (nothing while `update_check` is off; once a day unless `force`; stores `update_latest` and `update_checked`), `check_if_due` (the periodic one: a failure is logged), `forget` (off: the result goes too), `available` → `Release` when newer, `latest_version`. |
| `services/demo.py` | F30. The sample set: `seed(ctx)` writes a year of example records through the services (CHF only without rates), `receipt` makes a PDF proof; `is_empty` (no table holds a record), `first_run` (not `onboarding_done` and empty), `finish_onboarding`, `load` (refuses a folder with records; seeds, clears the history, marks `sample_data`, reloads the store) and `remove` (record files, attachments and history go, settings stay, both flags reset). `scripts/demo_data.py` wraps it for `just demo`. |
| `services/templates.py` | Transaction templates (F06): save, list, delete, apply to a form input; exchanges and revaluations are excluded. |
| `services/payees.py` | Payees (F09): `resolve(raw)` (an alias or name in the table first, then `normalize`; never a second spelling of a stored payee), `ensure(name)` (the stored spelling, or a new payee), `learn(pairs)` (imports: the payee plus the text's alias, taken from any other payee), `names` (recently used first, for the form), `usage` (count, spent and received per payee), and `rename`, `merge`, `delete` (unused payees only), each one undoable action that rewrites transactions, templates and series together. |
| `services/tags.py` | Tags (F10): `encode(entries)` (tag ids or names → a tags column, new names pending, nothing written), `store(column)` (creates the pending tags), `ensure`; `add`, `rename`, `recolour`, `merge` (records move to the tag kept), `delete` (the tag comes off every transaction, template, series and tax section), `set_budget`, each one undoable action; `tagged_amounts` (the money that counts per tag: own share of expenditure spent per part, so a split line's tags count that line (F11), income other than repayments received), `transaction_tags` (a transaction's tags and its lines'), `totals(view, start, end)` and `budget_status(tag)`. See §6, *Tags*. |
| `services/goals.py` | Savings goals (F14): `add`, `edit`, `archive`, `restore`, `delete` (each one undo step); `monthly_spending` (average recorded own spending of the last 12 complete months, from the first month with a transaction), `target_of`, `account_balance` (latest month-end balance plus the bookings after it up to today; holdings for securities and crypto accounts), `current_of` (linked account at the latest rate, else `saved`), `status`/`statuses`, `top_open`, `reserve_series`/`reserve_at` and `free_wealth` (decision 23). |
| `services/scenarios.py` | What-if scenarios (F15): `add`, `edit`, `set_shown`, `delete` (with the changes), `add_change`, `edit_change`, `delete_change` (each one undo step); `validate_change` (amount ≥ 0, currency, a budgetable category, months through `budget.plan_months`), `change_months`; `overlay(scenario, month, type, cur)` (the plan's budgets with the scenario's changes laid over, the later change winning), `projected_net`, `projected_wealth`, `series` (the projected months), `outcome` (plan and scenario at the end of the plan); `adopt` (every change checked, then written with Replace in entry order, the scenario removed, one undo step). Writes nothing into the plan but `adopt`. |
| `services/reminders.py` | Reminders (F31), in the app only: `KINDS` (`balances`, `recurring`, `bills`, `budgets`, `goals`) with `TITLES`, `DESCRIPTIONS` and `LINKS`; one builder per kind (`balances` from `wealth.account_rows`' `needs_entry` for last month, `recurring` from `recurring.due`, `bills` from `bills.due`, `budgets` from the `over_budget` and `pace` items of `trends.notable`, `goals` from `goals.statuses` with status `behind` or `overdue`), each a `Reminder` (kind, title, sub, link, amount, currency, tone `late` or `warn`); `reminders(ctx, view_cur)` per kind in order, kinds turned off and empty kinds left out; `count` (the sidebar badge); `enabled` / `set_enabled` (the setting `reminders_off`). Computes, stores nothing, sends nothing. |
| `services/tax.py` | F20. `links` (section → linked tag ids; a tag in two sections, after a merge, counts in the first) and `set_links` (tag ids or new names per section, a tag in one section only; one undoable action), `years`, `default_year` (last year), `summary(ctx, year)` → `Summary` of `SectionSummary` (entries, total, withholding tax, attachments) of `Entry` (date, payee, category, CHF amount signed for the section, note, tax withheld, transaction id, the tags that put it there, its attachments); `overlapping` counts transactions in more than one section. See §7, decision 25. |
| `services/tax_export.py` | F20. The summary as CSV (`summary_csv`, formula-safe), as PDF (`summary_pdf`: a summary page, then a table per section with date, payee, category, amount, tax withheld for the securities list, and the attachment names as *Proof*; page footer) and `proofs_zip` (the PDF, the CSV and the attachment files of the listed rows in one folder per section, `NN Section/<date> <payee> - <file name>`; each file read through `attachments.verified`, files that are missing or fail the check left out and named, at most 1 GB). |
| `services/rules.py` | Categorization rules (F07): `validate` (text of at least two characters, expenditure or income, a category of that type, an optional regular account), `save` (refuses a second rule with the same folded text and type unless asked to replace it), `delete`, `same_text`, `category_label` and `prefill` / `from_transaction` (the payee, or the name `normalize` finds in the note, with the category); each change one undoable action. |
| `services/categorize.py` | Category suggestions (F07): `Suggester` reads the rules and the history once and answers `suggest(type, payee, text, account=None)` with a `Suggestion` (category, account, translated reason, source) or None; `suggest(ctx, …)` is the one-off shortcut. Writes nothing. See §6, *Category suggestions*. |
| `services/importer.py` | CSV import: encoding, separator and header detection, column mapping and bank profiles, duplicate marking, category suggestions (`categorize`, marked on the row with their reason; `clear_suggestions`), saving in one write. |
| `services/camt.py` | camt.053 / camt.054 statements (any version) read into the importer's rows; the account remembered per IBAN. |
| `services/import_balances.py` | Month-end balances from a bank file (F36): the order of a CSV balance column (newest or oldest first) and the balance after each month's last booking, or a camt closing balance; compared with recorded balances and saved with `wealth.upsert_entry`. |
| `ui/theme.py` | Design tokens (light and dark), Quasar and AG Grid overrides, chart styling. See `docs/DESIGN_REVIEW.md`. Text tokens keep WCAG AA contrast on every surface in both appearances, checked by `tests/test_contrast.py` (F32). |
| `ui/layout.py` | Sidebar frame, sheets (dialogs), segmented control, month stepper, metrics, formatting (`1'234.50 CHF`), date picker. Keyboard (F32): `SHORTCUTS` (the list the shortcuts sheet shows), the page keys of `frame()`, Ctrl/⌘ Enter in `sheet_buttons`, `open_shortcuts()`, and `named()`, which gives an icon-only button its tooltip and `aria-label`. |
| `ui/widgets/` | Searchable selects, the payee field (`selects.PayeeInput`: a `ui.input` with the component `resources/payee_input.js`, a Quasar select used as text autocomplete, so the typed text is the value and the known payees, recently used first, are suggested while typing, matched anywhere and ignoring case and accents), transaction form (page, add sheet and edit sheet), AG Grid helpers. `selects.CategorySelect` is the category picker (F08): a `ui.select` subclass whose component `resources/category_select.js` (NiceGUI's `select.js` with another filter and option template) filters on each option's pre-folded `search` text in the browser, so typing is instant and needs no round trip; options show the official name as a second line and a history icon when recently used. F40: the filter also accepts the option's `note_search` words and lists those matches after the matches by name (the test helper `offered()` in `tests/ui/test_category_picker.py` mirrors it); the component loads the notes once per page from `notes-url` (`/coicop-notes/<lang>?v=<hash>`, served by `selects.add_notes_route()`, cached immutably) when the picker first opens, shared by every picker on the page, and shows an option's notes in a `q-tooltip` on hover (after 350 ms) or when the arrow keys reach it; on touch screens (`$q.platform.has.touch`) an info button in the option opens them inside the option instead, and stops its tap from selecting. Used by the transaction form, both budget forms, the Budget Plan edit sheet, the bulk edit sheet and the import page (with the income categories as extra options). The value stays the COICOP code (or `TAX`). `bulk_edit.BulkEditSheet` is the bulk edit sheet (F13): one method per field, re-planned with `bulk.plan` on every change. `rule_sheet.open_rule_sheet` adds or edits a categorization rule (F07), `always_like_this` opens it from a transaction. |
| `ui/widgets/tags.py` | F10. `TagSelect`: a multiple `ui.select` whose chosen tags are chips in their colour (Quasar slots `selected-item` and `option`), a name typed with Enter added as it is (a new tag once saved; a typed name of an existing tag picks it, ignoring case); `chip`, `ColourPicker` (one swatch per palette colour) and `CHIPS_RENDERER`, the AG Grid cell renderer that builds the chips as elements from the row's `_tags`, so names are never HTML. |
| `ui/widgets/notable.py` | F18. `notable_card(ctx, currency)`, the Overview's *Notable This Month* card: one link row per item (icon, title, reason, amount, chevron) to `link(item, month)`; `texts(item, notable)` words each kind. |
| `ui/widgets/attachments.py` | F12. `AttachmentList`, the *Attachments* section of the transaction form (see §6), and the route `/attachment/{id}` (`register`, `serve`, `url`); `add_pending` starts a new transaction with a file (F04). |
| `ui/widgets/receipt.py` | F04. `open_receipt_sheet(ctx, on_saved)`: the *Receipt* sheet (drop zone, "Reading the receipt…", then the picture beside a `TransactionForm` started with `receipts.prefill`, its `items` and the file as `files`); `summary(receipt)` is the caption above the form. |
| `ui/pages/quick_add.py` | F35. The phone's pages, without the sidebar: `pair_page` (`/pair`, outside the lock: the pairing code) and `page` (`/quick`, behind the lock for a phone session: one expense with amount, account, category, payee, date and note). |
| `ui/pages/welcome.py` | F30. The first-start screen (`/welcome`), without the sidebar: language, display currency, data folder, *Open Settings* and *Load Sample Data* (`load_sample`, shared with Settings: runs `demo.load` in a worker thread with the button in its loading state, then opens the Budget page). |
| `ui/pages/` | One module per page (see §6). |

## 3. Data model (`data/`)

Every money record stores **the original amount and currency, the amount in CHF, and the
ECB date of the rate used** (`fx_date`).

| File | Columns |
| --- | --- |
| `accounts.csv` | `name, currency, kind (regular/advance), created, asset_class, invest_from, id` – `asset_class` (F21): `cash`, `securities`, `pillar2`, `pillar3a`, `real_estate`, `crypto`, `other` or `loan` (F22: a loan's liability account, added with its loan); files written before it have no such column, which reads as `cash`. Advance-payment accounts are always `cash`. `invest_from` (decision 13): the month (`YYYY-MM`) an account with history became a securities or crypto account; empty for accounts that were one from the start, and in older files. |
| `income_categories.csv` | `name, id` |
| `transactions.csv` | `date, type, amount, currency, amount_chf, fx_date, category, from_account, to_account, to_amount, to_currency, is_advance, advance_amount, advance_account, debtor, settles_id, comment, import_ref, linked_id, recurring_id, payee, invest_id, tags, loan_id, id` – `recurring_id` names the recurring transaction that booked the row (F05; empty in files written before it); `payee` is the name of a payee in `payees.csv` (F09; expenditure and income only, empty in older files); `invest_id` is the id of the trade or price in `trades.csv` / `prices.csv` that booked the row (F21; empty otherwise); `tags` are the ids of the row's tags in `tags.csv`, separated by `\|` (F10; empty in older files and for spread bookings); `loan_id` names the loan whose payment booked the row (F22: the repayment transfer or the interest expenditure; empty otherwise and in older files) |
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
| `goals.csv` | `name, currency, kind, target, months, target_date, account_id, saved, reserve, archived, start, start_amount, id` – one row per savings goal (F14). `kind` `amount` aims at `target`; `months` at `months` × the average monthly spending of the last 12 complete months, both in `currency`. `target_date` is `YYYY-MM-DD` or empty. With `account_id` the progress is that regular account's balance, else `saved` (typed in). `start` and `start_amount` are where the on-track line begins: the day the goal was added (or linked to another account, or given another currency) and what it held then. `reserve` (0/1) puts it into the free-wealth line; `archived` (0/1) takes it out of the lists, the Overview and the reserve. |
| `scenarios.csv` | `name, comment, shown, created, id` – one row per what-if scenario (F15); `shown` (0/1) draws it on the Overview's wealth chart. |
| `scenario_changes.csv` | `scenario_id, type, category, amount, currency, amount_chf, fx_date, start_month, end_month, recur_months, comment, seq, id` – one change of a scenario (F15): in the months from `start_month`, every `recur_months` months, up to `end_month` (empty: the end of the plan) the budget of `category` (`type` `expenditure` or `income`) is `amount` instead of the plan's; 0 takes it out. `seq` is the order of entry (the later change counts where two meet); `amount_chf` at the latest rate when saved, displays convert like budget entries. |
| `subscription_ignores.csv` | `key, currency, name, id` – a detected subscription the user told the Subscriptions page to leave out (F17): the group it was found by (a payee's lookup key, or `note:` and the folded note) with its currency, and the name shown. |
| `tax_sections.csv` | `tags, id` – the tags linked to a section of the tax summary (F20): `id` is the section's key (`professional`, `pillar3a`, `insurance`, `medical`, `donations`, `childcare`, `debt_interest`, `withholding`, `securities`), `tags` tag ids separated by `\|`. A section without a row has no tags; deleting or merging a tag rewrites it like any record. |
| `prices.csv` | `holding_id, date, price, id` – a price typed in or pasted (F21), one per holding and day. |
| `loans.csv` | `account_id, principal, start, rate, first_due, next_due, recur_months, amortization, cash_account, interest_category, lender, fixed_until, comment, id` – one row per loan or mortgage (F22): its liability account, the debt on `start` (0 when the payout is booked as a transfer from the account), the rate in percent a year, the first payment date and the first one not yet booked or skipped, the rhythm in months, the repayment per payment (0: interest only), the regular account the payments come from, the COICOP code (or `TAX`) of the interest, the lender (a payee name), `fixed_until` (`YYYY-MM-DD` or empty). |
| `settings.json` | `budget_horizon, display_currency, last_fx_download, language, onboarding_done, sample_data, update_check, update_checked, update_latest, reminders_off, projection` – `onboarding_done` and `sample_data` (F30): whether the welcome screen was passed, and whether the folder holds the sample set; `update_check` (F33, off by default), the day of the last check and the tag it answered with `reminders_off` (F31): the kinds of reminders turned off, a list of keys of `services.reminders.KINDS`; missing in older files, so every kind is on. `projection` (decision 36): how the projection estimates a month without balances, `higher` (F47, also for files from before the setting) or `budget`. |
| `security.json` | F27, plain JSON, never backed up or encrypted: `version`; `password` (`salt`, `n`, `r`, `p`, `verifier`); while encryption is on, `encryption.password.wrapped` and `encryption.recovery` (`salt`, `n`, `r`, `p`, `verifier`, `wrapped`); `phone.enabled` (F35). Absent while the lock is off. With encryption on, every other record file (the tables, `settings.json`, `history.jsonl`, `import_*.json`, also in `backups/`) is `BUDGET-ENCRYPTED-1\n` followed by a Fernet token. |
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

**Who owes what** (F23) is read from these rows, with no table of its own: a person is the folded `debtor`
of their advances; a repayment counts for the debtor of the advance it settles (its own `debtor` is a copy
taken when it was saved).

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
| After each rate check (start, then every 30 minutes) | Every transaction's `amount_chf` is recomputed at the last ECB date on or before its date and stored where the date or the amount differ: entries saved by older versions (latest rate), entries made before the ECB published that day, future-dated entries, and amounts that don't match their rate (a hand-edited file). Exchange target amounts are kept (`transactions.revalue_to_transaction_dates`). Every account entry likewise at the last ECB date within its month (`wealth.revalue_entries`), which catches up a balance of the running month recorded before its last rate. So the first start of a new version values everything recorded with the correct rate (decision 36) |
| Saving a budget entry (`amount_chf`) | Last (most recent) row of the ECB file; that date is stored as `fx_date` |
| Saving an account entry | Last ECB date within that month (re-valued after each rate check, see above) |
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
  - a month's **expected net** (`aggregates.projected_net`) follows the rule chosen in Settings →
    *Projected Wealth* (`settings.projection`, decision 36):
    - *Recorded where higher* (`higher`, F47, the default): per top-level expenditure row (COICOP
      division, taxes, advances) and per income category, the **higher of recorded and budgeted**:
      overspending or extra income that is already recorded counts, and a budget not yet used still
      counts. Advances and repayments have no budget and count as recorded. A split transaction (F11)
      is recorded per part, each in its own division (§4.3);
    - *Budget only* (`budget`): the month's budgeted income minus its budgeted expenditure (the same
      top-level rows), whatever is recorded;
  - either rule applies to every month after L, including months that are over but have no balances
    yet (decision 5 of the catalog), and to a what-if scenario's line (F15) with its budgets;
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
- **Budget from history** (F16, decision 24; `domain.proposal`, `services.budget_history`):
  - the **basis** is the last 3, 6 or 12 complete months (before the current month), without the months
    before the first expenditure or income, so a history that starts later counts only its months;
  - per category and basis month, in **CHF** at each transaction's own rate (`amount_chf`): expenditure per
    part (§4.3, split lines in their own category) without the *Advance payments* share, income without
    advance repayments; transfers, exchanges and revaluations don't count. Expenditure codes are folded to the
    chosen COICOP level (division, group or class); a code that isn't deeper, and *Taxes*, stay as they are;
  - every basis month counts, an empty one as 0; the **average** or the **median** (of an even number of months,
    the mean of the middle two), rounded half up to the chosen step and never below 0;
  - rows are the categories with a cent or more in some basis month and a budgetable category (a COICOP code or
    *Taxes*; an income category that exists);
  - **conflicts:** budget entries of the same type and category in the target months (From to Until, every
    month; *Forever* means up to the horizon). Ticking such a row creates its series with *Replace*, which
    deletes them first; months outside the target keep theirs. Budgets of a category inside or above the row's
    are **overlaps**: they would be counted together with the new one in the roll-up, so the row starts unticked,
    but ticking it adds alongside;
  - **recurring series** (F05) that have not ended and book in the row's category at that level are listed with
    the row; they don't change the amount or the default tick;
  - creating checks every ticked row first, then adds one monthly CHF series per row (note "From history:
    average of *first – last month*") in one recorded action.

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

### 4.7 Loans and mortgages (F22)

- **Debt:** a loan's liability account (asset class `loan`) owes `principal` on `start`; its outstanding debt after
  a day is the principal plus every transaction *from* the account (a drawdown) minus every transaction *to* it (a
  repayment; an exchange counts its `to_amount`). The account's month-end balance is minus that debt
  (`loans.value_entry`, through `wealth.balance_entry` like a securities account's, never recorded: `upsert_entry`
  refuses it), from the month of `start` on. The loan's start month counts as a recorded month for the start of
  the wealth series, not for the projection's start. Wealth and *By Asset Class* therefore show net worth; the
  class bar splits the assets and lists the loans as negative amounts.
- **Payments** fall due every `recur_months` months from `first_due` on (month-end rule of §4.5); `next_due` is
  the first not yet booked or skipped, and every due payment up to today is listed. The **interest** of a payment is
  simple interest for the period on the debt before the due date: `round_half_up(debt × rate ÷ 100 × months ÷ 12,
  2)` (the 30/360 convention Swiss mortgages are quoted in); the **repayment** is `amortization`, or what is left
  of the debt. Later due payments of the same loan are planned from the first one's result. A bank's rounding may
  differ by a few rappen: the amounts are edited before booking, like a recurring transaction's.
- **Booking** (`loans.book`, one undoable action) writes, dated the due date unless changed, a transfer of the
  repayment from the paying account into the loan account and an expenditure of the interest from the paying
  account in the loan's interest category (default 12.2.1, see decision 34) with the lender as payee, through
  `transactions.save`, each with `loan_id`. Nothing is booked for an amount of 0; a repayment above the debt is
  refused. The rows are ordinary transactions: editable, deletable, bulk-editable; `prepare_edit` keeps `loan_id`.
  Skipping advances `next_due` without booking. A paid-off loan (debt ≤ 0) has no due payments.
- **Reconciliation:** the loan account's opening is 0 in its start month (so the principal shows as its unexplained
  change: the payout is not in the app, like a newly recorded account's first balance), and from then on closing −
  opening − flows = 0 for every month, since the balance is the flows (tested in CHF and EUR views).
- **Editing** a loan keeps the payments booked; the schedule continues after the last payment handled, on the new
  terms (`planned_next_due`, like a recurring series); a loan that handled none starts with the new first date. The
  currency can't change while transactions use the account. **Deleting** a loan deletes its account as well, and is
  refused while transactions use the account or carry its `loan_id`, or a recurring series uses the account.

## 5. Exchange-rate refresh

At startup, and every 30 minutes while the app runs, `FxService.refresh_if_stale` compares
`settings.last_fx_download` with today. If they differ, it:

1. downloads the ZIP, capped at 20 MB, with a 100 MB cap on the contained CSV;
2. validates the CSV in memory (nothing is extracted to disk by name);
3. atomically replaces `data/ecb/eurofxref-hist.csv` through a unique temp file;
4. stores today's date.

A lock prevents overlapping refreshes.

**Update check (F33):** after each refresh, `updates.check_if_due` asks GitHub's releases API for the newest
release's tag, but only when `settings.update_check` is on (off by default) and not yet done today. The answer
is read in memory (1 MB cap), only a tag of the form `v1.2.3` is kept, and the link to the release page is built
from it. Nothing is downloaded; a failure is logged and tried again at the next refresh. The sidebar links the
release when it is newer than `config.app_version()`.

If the download fails, the previous file stays in use and a warning banner is shown. The
currency dropdowns list every currency the ECB quoted on its latest date, plus EUR and CHF.

## 6. Pages

**Places and tabs** (decision 42, the UX review before 1.0, `docs/UX_REVIEW.md`). The sidebar lists seven places;
a place with several pages shows them as tabs under the title (`layout.SECTIONS`), and a page that is no tab
highlights the tab it belongs to (`layout.TAB_PARENT`):

| Place | Tabs |
| --- | --- |
| Overview (`/`) | – |
| Budget (`/budget`) | – |
| Transactions | Transactions (`/transactions`) · Recurring (`/recurring`) · Bills (`/bills`); Import (`/import`) and New Transaction (`/add-transaction`) belong to the first |
| Plan | Budget Plan (`/budget-entries`) · Goals (`/goals`) · Scenarios (`/scenarios`) |
| Wealth | Accounts (`/accounts`) · Investments (`/investments`) · Loans (`/loans`) · Shared Expenses (`/shared`) |
| Reports | Year in Review (`/review`) · Tax Summary (`/tax`) |
| Settings | General (`/settings`) · Categories (`/categories`, with the income categories) · Payees (`/payees`, with its Rules, `/rules`) · Tags (`/tags`) |

The sidebar shows one badge, beside Overview: what is due. The counts of due recurring transactions, bills and
loan payments sit on their tabs. Every page uses the same column, so the title doesn't move between pages; a
narrow page keeps its lists to 46 rem, left-aligned under the title.

| Route | Page | Notes |
| --- | --- | --- |
| `/` | Overview (start page) | Current wealth and the projection at the horizon in one hero card; below it, once any account has another asset class than cash, *By Asset Class* (F21): wealth at the end of last month as one bar split by class, with each class's amount and share; then the *Goals* card (F14, see `/goals`). Chart builder ("Add Chart" menu): wealth line (recorded, then dotted projection, and with goals reserved in the projection a dashed *Free after goals* line, F14, and one dashed line per scenario marked *shown*, from the last recorded value, F15), income/expenditure bars or lines with category multi-select and optional budget, waterfall of per-account change, ECB exchange rates of chosen currencies in the display currency (daily or month end, optionally indexed to 100), wealth by asset class (stacked bars per month, F21), spending by tag (one bar per tag in its colour, the own share of tagged expenditure between two months, most first, F10). Below the wealth card, *Notable This Month* (F18, `ui/widgets/notable.py`): up to five items of the current month (exceeded budgets, large payments, categories well above their usual level, budgets on pace to be exceeded), each a link to the Budget page or the filtered Transactions; hidden when nothing stands out, a caption instead while fewer than three months are recorded. Each chart's "…" menu saves a PNG, exports CSV or Excel, or removes it. |
| `/budget` | Budget | Month stepper (‹ month ›) and currency. A callout names accounts without a balance for a past month. Metric strip (current wealth, wealth or projected wealth at month end, net income). *Compare with* (F18): a segmented control *Budget · month before · same month last year*; comparing, the budget column shows the other month's amount, the difference column the change (▲/▼ and %, amount in the tooltip; below 560 px it replaces the amount), the caption the change of the total, and meters, pencils and *Budget Another Category* are hidden. `/budget?month=YYYY-MM&category=<code>` opens a month with the category's row opened, marked and scrolled into view. Income and expenditure ledgers share one column layout: budget meter, actual, budget, difference; zero values show as a dash. The page may grow to 100 rem; the two ledgers sit side by side once their container is 1,500 px wide (each about 750 px), else below each other (B08, decision 1). Container queries on each ledger drop the meter from 800 px and the meter and difference below 560 px. The expenditure tree expands by clicking a category. Budget amounts are editable (pencil on hover) in incomplete months: amount, currency, until (or forever), repeat and note per entry; changing until or repeat replans the series from that month on (`budget.reschedule`), the earlier months keep their values and end there. Balances list for past months. Statement export sits in the share menu. "Record Balance" opens the balance sheet. |
| `/add-transaction` | New Transaction | The same form also opens as a sheet from the sidebar's "New Transaction" button on every page; the page behind refreshes after saving. Type first (segmented control), then amount, currency and date, then the type's fields. Account dropdowns are filtered by currency. Live CHF preview. Save stays disabled until the form validates, and the reason is shown once the user has started. After saving, every field except date, currency and type resets. Expenditures and income can carry a product list (item, quantity, unit price; negative prices for discounts): Enter on a price starts the next line, item names are suggested from earlier lists, and the list's total is compared with the amount ("Use Total as Amount") but never changes it on its own. F11: each line of an expenditure has a split button (grey, blue once the line has its own category or tags) that opens a row below it with the category picker (`CategorySelect`, "Same as the transaction" while empty) and a tag field for that line; a line that has them shows them when the form opens, and the pickers are built only then, since each carries every category. Below the list, "Rest in 01.1 Food: 23.40 CHF" (or "the transaction’s category" before one is chosen) shows what stays in the transaction's category; when lines with their own category exceed the amount it says so in red, and Save refuses it with the same message. For income the split buttons are hidden, and lines that still carry a category say they lose it when saved. Faster entry (F06): the amount fields take sums (`12.50+8.30`, evaluated safely, shown as the result when the field is left); a templates menu beside the type saves and applies named templates (`templates.csv`); recently used accounts come first; ⌘↩ / Ctrl+Enter saves, and N opens a new transaction on every page; *Duplicate* in the edit sheet and the Transactions selection bar copies a transaction (with its items) dated today. Expenditures and income have a *Payee* field (F09) below the date: free text with the known payees as suggestions (recently used first); a new name becomes a payee when saved. Templates and duplicates carry the payee. A new expenditure or income with a payee and no category shows the suggested category under the category field (F07): "Use Groceries · 01.1" and the reason ("Used 14 times for this payee", "Rule: contains “migros”", plus the account when one would be filled in); a click fills the category and, if still empty, the account. The edit sheet offers *Always Categorize Like This…* there instead. Under the note, *Attachments* (F12, every type): a drop zone (drop files, or click to choose several; the picker offers the accepted types, and the uploader refuses larger files before sending them), then per file a thumbnail (photos) or icon, name, size, date and *Open*, *Save a Copy…*, *Remove*; clicking the name of a photo or PDF toggles a preview below the list (an `<img>`, or the PDF in an `<iframe>`, which WKWebView, WebView2 and Qt WebEngine render; headless Playwright WebKit leaves it blank). In the edit sheet each file is stored or removed at once, with its own Undo toast, and the list behind the sheet refreshes; a new transaction keeps the files in memory and stores them inside its save action, so they share one Undo. |
| `/transactions` | Transactions | Search field, filters (date range, amount range, account, payee, type) behind "Filters". AG Grid with the payee (F09), signed, coloured amounts, the number of items per transaction, a paper clip with the number of attachments (F12; the header shows the clip, exports say *Attachments*), the rhythm of the recurring transaction that booked a row (*Repeats*), sort on every column. The search also finds item names, payees and attachment names. A split expenditure (F11) shows its categories as "01.1 Food + 2 more" (its own category first), the search finds every category of its lines, and the *Tags* column and the tag filter include its lines' tags. Selecting rows reveals Edit (one row: the editor; several: the bulk edit sheet, F13, see below), *Make Recurring…* (one expenditure, income or transfer without an advance: a series that continues it, with the transaction as its first booking), *Always Categorize Like This…* (one expenditure or income with a category: the rule sheet, F07) and Delete; double-click edits in a sheet. "Import…" opens the CSV import. The share button exports the rows the filters show as CSV or Excel. A *Tags* column (F10) shows each row's tags as coloured chips (an AG Grid cell renderer; sorting, the column filter and the export use the names as text); *Filters* has a tag filter (rows with any of the chosen tags), the search finds tag names, and `/transactions?tag=<id>` (from the Tags page) opens the list filtered by that tag. *Filters* has a *Category* filter (F18, the category picker): expenditure in that category or below it, a split counting when any of its parts does; `?month=YYYY-MM&category=<code>` (from *Notable this month*) opens the list with From, To and the category set. *Receipt…* (F04) opens the *Receipt* sheet: a drop zone for one PDF or photo, then the picture on the left and the transaction form on the right, started with what `receipts.read` found (amount, date, currency, payee, item lines that add up) and the file waiting under Attachments; the caption says what was read, or that the engine isn't available and the rest is typed from the picture (§7, decision 29). |
| `/recurring` | Recurring | F05. In the sidebar after Transactions; the sidebar shows the number of due occurrences as a badge. *Due*: one row per due occurrence, oldest first, with title, date and type, the amount and date editable, *Book* and *Skip* (later occurrences of the same series wait for the first); *Book All* books every due row with the amounts and dates as edited. *Series*: title, amount, rhythm, next date or *ended*, end date, *books automatically*, last booking, note; edit and delete per row. The sheet (new, edit, *Make Recurring…*) has type (expenditure, income, account transfer), amount and currency, category, accounts (filtered by currency), first date, repeat, until, *Book automatically* payee (expenditure and income) and note, and says when the next occurrence falls; a series with a payee is titled "*payee* · *category*"; saving an automatic series books what it has due in the same undoable step. |
| `/subscriptions` | Subscriptions | F17. In the sidebar after Recurring (icon `o_subscriptions`). *Detected*: one row per payee charging at a rhythm (name; "49.90 CHF · Every month · 8 charges since 15 Mar 2026 · last on 15 Oct 2026 · next around 15 Nov 2026"; what it costs a year), the header summing the year in the display currency; *Make Recurring…* opens the series sheet of `/recurring` prefilled from the last charge (which becomes the series' first booking), *Ignore* (undoable). *Recurring Transactions*: the running expenditure series of F05 with amount, rhythm and yearly cost, their total, a link to All Recurring. *Ignored* (closed expansion): *Watch Again*. The page refreshes when a sheet saves. |
| `/bills` | Bills | F03. In the sidebar after Subscriptions (icon `o_request_quote`), with a badge counting the open bills that are overdue or due within 7 days (`layout.BADGES`; Recurring has the other). A drop zone ("Drop QR bills (PDF or photo)", or click to choose; a hidden `ui.upload` like the attachments' one) scans each file with `bills.scan` in the event loop (about 0.1 s for a PDF page; PDFium is not thread-safe) and reports what it added, what was already in the list and why a code was refused, prefixed with the file name. *Open*: one row per open bill, the earliest due first: creditor and amount (or "Amount to fill in (CHF)"), the due date ("Overdue since …" in red, "Due today" and "Due on …" within a week in orange, "No due date"), message and formatted reference, the file as a link (`/attachment/<id>` in a new tab; the system viewer in the app window); *Mark Paid…* opens the transaction sheet titled *Pay Bill* with `bills.payment_input` and `bill_id`: the form suggests the category (F07), lists the bill's file under Attachments ("Attached when you save", without Remove), its button is *Mark Paid*, it checks `bills.check_payment` before saving and calls `bills.paid_with` inside its save action; a calendar menu sets or clears the due date; Dismiss and Delete (with a confirmation saying what happens to the file). *Paid and Dismissed · n bills*: a collapsed `ui.expansion` with *Show Payment* (the edit sheet of the payment) or *Reopen*, and Delete. Every action is undoable from its toast. |
| `/reminders` | Reminders | F31. In the sidebar after Bills (icon `o_notifications_none`), with a badge counting the items (`layout.BADGES`, refreshed with the other two). A caption says what is listed and that nothing is sent. "{n} reminders", then one group per kind with items ("Balances to record · 1"): each row a link (the *Notable this month* row style) with an icon in the tone's colour, title, the line below (red when overdue or exceeded), the amount and a chevron, to the Budget page at the month (balances) or the category (budgets), Recurring, Bills or Goals. "Nothing to do right now." when there is nothing. Refreshes when a sheet saves data. |
| `/import` | Import Transactions | 1 · file: encoding (UTF-8, Windows-1252), separator and header line (bank preambles are skipped) are detected. 2 · columns: account, date column and format, one signed amount or debit/credit columns, decimal style, description columns, an optional balance column (detected from names such as *Saldo*, *Kontostand*, *Balance*, *Solde*), sign inversion for card statements; the mapping can be saved per bank (`import_profiles.json`) and is recognised by the header line next time. 3 · review: rows already imported (same `import_ref`) or matching a manual entry (same account, day, type and amount) are left out; rows without a category get the suggested one (F07), in italics with the reason in a *Suggestion* column, counted in the summary ("2 categories are suggestions (in italics)") and removed together with *Clear Suggestions*; a category applied by hand loses the mark, and changing a row's payee replaces its suggestion; the *Payee* column (F09) shows `payees.resolve` of the counterparty column (a description column named *Empfänger*, *Payee*, *Merchant*, *Bénéficiaire* …) or of the text, and can be edited (double-click); importing adds missing payees and remembers each text as an alias of the payee it was imported with; selected rows get a category, are included or excluded in bulk. Saving is one write; nothing is saved while a row is invalid. **camt.053 / camt.054 (XML, F02):** recognised by content, read namespace-agnostically (versions .001.02 to .001.08 and later) with the standard library after a pre-scan that refuses any DTD (so no entities), capped at 20 MB like CSV. The columns step is skipped: one row per booked `Ntry` (pending entries are left out and counted), booking date, signed amount (`CdtDbtInd`), text from counterparty name, `Ustrd` and `AddtlNtryInf`, the payee from the counterparty name (`RltdPties`, else the text); a batch whose `TxDtls` each carry an amount adding up to the entry becomes one row per detail. `import_ref` is `camt:<IBAN>:<AcctSvcrRef \| NtryRef \| content hash>`, so the same or an overlapping statement shows its entries as already imported; a row matching a manual entry or one imported from the other file kind (CSV ↔ camt) on the same day, type and amount is a possible duplicate. The account is chosen in the review step, preselected from `import_accounts.json` (IBAN → account id, written after a successful import); a statement whose currency differs from the account's is refused. The statement's IBAN and period are shown. **Month-end balances (F36):** below the grid, the review lists "Balance at end of *month*" with a checkbox and any recorded balance it would replace. From a CSV balance column (`ImportRow.balance`), the order of the lines is the reading (file order or reversed) in which the dates run forward and each balance equals the previous one plus the amounts in between (lines without a balance are bridged); the balance after the last booking of each month covered, months without bookings included, is offered. A month is complete, and ticked, when the file continues past it or its last booking is on the month's last day; the month the file ends inside is offered unticked with "the file ends on *date* — not a month-end balance" (decision 6). When no reading fits, or both fit with different results (one-day files), nothing is offered and the reason is shown. A camt statement offers its closing booked balance (`CLBD`, in the statement's currency), ticked when dated on a month's last day. A balance already recorded with the same amount starts unticked. After the transactions are saved, ticked balances are recorded with `wealth.upsert_entry` (month-end rate, replacing the month's entry); the button counts both ("Import 0 Transactions and 1 Balance" when every row is a duplicate). |
| `/rules` | Categorization Rules | F07. Not in the sidebar: *Rules…* on the Payees page and in Settings lead here, and the sidebar highlights Payees. One group per type (expenditure, income) sorted by text: "Contains “migros”" with the category and the account it suggests (or that the category or account no longer exists), edit and delete per row, *Add Rule…* per group and *New Rule*. The sheet has type, *Text contains*, category, optional account; when another rule has the same text and type it says so and the button becomes *Replace Rule*. Every change is undoable from its toast. |
| `/payees` | Payees | F09. In the sidebar after Categories. One group sorted by name: per payee its number of transactions and what was spent and received (all time, display currency); the search matches names and remembered bank texts. Rename (pencil) opens a sheet; ticking two or more reveals a selection bar with *Merge…*, whose sheet chooses the name to keep (the most used is preselected); payees no transaction, template or series names have a delete button. Each action is one undoable step with an Undo toast. |
| `/tags` | Tags | F10. In the sidebar after Payees (icon `o_sell`). One group sorted by name: per tag its chip (a click opens Transactions filtered by it), its number of transactions and what was spent and received (all time, display currency), and with a budget a meter and "540.00 of 2'500.00 CHF · 1'960.00 CHF left · 1 Jul 2026 – 31 Jul 2026" (red when over). *New Tag* and the pencil open a sheet with name and colour swatches; the piggy bank opens the budget sheet (amount, currency, optional From and Until, *Remove Budget*); delete asks first and says how many transactions lose the tag; ticking two or more reveals *Merge…*. Every change is one undoable step. |
| `/loans` | Loans | F22. In the sidebar after Investments (icon `o_credit_score`), with a badge counting the due payments (`layout.BADGES`). *New Loan*. *Due*: one row per due payment, oldest first (later ones of the same loan wait for the first), with the loan's name and date, the interest and the repayment as proposed by the schedule and the date, all editable, *Book* and *Skip*; *Book All* books every due row with the amounts as edited and reports the loans whose payment failed. *Loans*: one row per loan: name, "495'000.00 CHF outstanding", a bar of the share repaid (green once paid off), the terms ("1.5 % · 2'500.00 CHF every quarter + interest", or *interest only*), the status (*next on …*, *paid off in …*, *fixed until …*, *term ended*, or *Paid off*), the paying account, lender and note; edit and delete (confirmation) per row. A click opens the details sheet: outstanding, repaid (with the share), interest this year, the next 12 planned payments with the debt left after each, and the transactions booked (a click opens the transaction editor). The loan sheet (new, edit): name and currency (fixed while the account is in use), debt at start and start date, interest rate and rhythm, first payment and repayment per payment (empty: interest only), paying account (regular accounts in the currency, preselected when there is one), interest category (the category picker, 12.2.1 prefilled), lender (the payee field), fixed until, note; the caption previews the first payment and when the loan is paid off (and the interest until then), Save stays disabled with the reason until the input is valid. |
| `/shared` | Shared Expenses | F23. In the sidebar after Loans (icon `o_group`). A caption on what the page reads (the advance payments) and how to name a debtor. *N people owe you*: one row per person with open items, by their oldest item: name, "40.00 CHF + 45.00 EUR open · 2 items · oldest 12 Sep 2026", what was settled this calendar year, *Settle Up…*, *Summary…* (icon) and a toggle that folds out the open items ("12 Sep 2026 · Restaurant X · 40.00 of 60.00 CHF open"; a click or Enter opens the transaction editor); the people who owe nothing under a folded *Settled · N people*, each with *Summary…*. The settle-up sheet: currency (segmented, only when the person owes in more than one), *Amount Received* (prefilled with all that is open in it), date, *To Account* (regular accounts in the currency, preselected when there is one), note; the caption lists the items the amount settles or why it can't ("Ben owes 50.00 CHF, not more"), and *Record Repayment* stays disabled until it can; the toast "Recorded 30.00 CHF from Ben" offers Undo. The summary sheet: the text read-only, *Save as Text…*, *Save as PDF…*, *Copy*, *Close*; the app sends nothing. Icon-only buttons carry their tooltip as `aria-label`. |
| `/investments` | Investments | F21. In the sidebar after Tags. Buttons *Buy*, *Sell*, *Dividend*, *Update Price* and *Paste Prices…* (once a holding exists). A metric strip (value, money put in with dividends and fees, gain and %, in the display currency) and a caption with the formula; one group per securities or crypto account (name, currency, class) with a row per holding: name, ISIN/symbol, quantity × last price on its date, value, gain and %; a total row with two or more holdings; *Add Holding…*. Without such accounts it points to Settings. A holding row opens the holding sheet: its figures (quantity, last price, value, money put in, dividends, fees, realized, unrealized, gain), *Buy*/*Sell*/*Dividend*/*Update Price* for it, the history newest first with the gain or loss each event booked and edit/delete per trade or price, *Edit Holding…* and *Delete Holding…*. The trade sheet (segmented Buy/Sell/Dividend, holding, date, quantity, price, fee or amount received, cash account in the holding's currency, preselected from the last trade, note) says what it books and why Save is disabled ("Only 10 held on …"); the price sheet previews the value and the change; the paste sheet lists each line as read, with the lines left out and why. Opening a booked row in the transaction editor opens its trade or price sheet. |
| `/review` | Year in Review | F19. In the sidebar before Tax Summary (icon `o_auto_stories`): the two yearly pages close the list. Year picker (last year by default, this year as *so far*), display currency, share menu (*Save as PDF…* with the chart as the browser renders it, *Export as Excel…*). A metric strip (income, expenditure, *saved* as the hero with the savings rate, wealth at the end of the last complete month with its change since the end of the year before; the year before's figure under each), a bar chart of income and expenditure per month, then the groups *Spending by Category* (top-level rows with the year before, the share of the year's spending and the change as ▲/▼ and a percentage, red when spending rose), *Income* (red when it fell) and *Biggest Expenses* (the own share, a button opens the transaction). "Nothing recorded in 2026" for an empty year. The page refreshes when a sheet saves (`on_data_changed`). |
| `/tax` | Tax Summary | F20. Last in the sidebar (icon `o_account_balance`): used once a year and drawing on the others (categories, tags, investments), it stays out of the way of the daily places. A year picker (last year by default, the year a declaration is made for), *Sections…* (the sheet linking tags to sections: per section its categories or rule and a tag field) and the share menu (*Export as PDF…*, *Export as CSV…*, *Proofs as ZIP…*, the ZIP built in a thread). A note says that cantonal rules differ and the app judges nothing, and that sections can overlap. One collapsible row per section (`ui.expansion`) with its title, what it covers, its total in CHF and the number of rows and files; open, it lists the linked tags and its rows: payee (or note), date and category, the tag chips that brought a row in, the attachments (a link, or the system viewer in the app window), the amount (and the tax withheld) and a button that opens the transaction. The open sections are kept when the year changes. |
| `/add-budget` | New Budget Entry | Also a sheet on Budget Plan. Type, amount, currency, category, month range or "Forever", repeat interval, replace option, note. Live summary of the months that will be created. |
| `/budget-entries` | Budget Plan | Same list pattern as Transactions, with the same export. The edit sheet can apply changes to all later months of a series, or replan it with Until and Repeat. *Propose from History…* (F16, `ui/pages/budget_proposal.py`) opens a sheet: basis (3, 6, 12 months) and method (Average, Median) as segmented controls, Level, Round to, From and Until; a caption with the months counted; per group (Expenditure, Income) a list of rows, each with a checkbox named after the category, the months' values in whole francs (tooltip with the months), conflict, overlap and recurring notes, *Budget now* and the editable amount per month. Every input recalculates (`budget_history.propose`); amounts typed by hand survive a change of From or Until only. The footer is sticky: the sum of the ticked rows per month (or why nothing can be created) and *Create Budget for N Categories*; the toast "Created N budget entries" offers Undo. On narrow windows (Tailwind `max-sm`) a row stacks its amount under the category and hides an empty *Budget now*. |
| `/goals` | Goals | F14. In the sidebar after Budget Plan (icon `o_flag`). One group sorted by target date (without one last): per goal its name, "1'250.00 of 4'000.00 CHF · 31 %", a bar (green once reached), the plan line ("562.50 CHF a month until 30 Jun 2027 · on track", *behind* and *Overdue since …* in red, *Reached* in green, "… to go · no target date") and where the progress comes from (account or by hand, the months of spending, *reserved in the projection*); edit, archive and delete per row; archived goals in a closed expansion with *Restore*. The sheet: name, *Amount* or *Months of Spending* (with the target worked out live), currency, optional target date, linked account (or *Saved So Far*) and *Reserve in the projection*. The Overview shows the first three goals not archived (still to reach first, by date) in a *Goals* card below the KPIs, with "Projected for {month}: … free after … reserved for goals" when a goal reserves; the card is left out without goals. |
| `/scenarios` | Scenarios | F15. In the sidebar after Goals (icon `o_alt_route`). One block per scenario, by name: name and note, a switch *On the Overview*, edit and delete; the line "Projected for Dec 2027: 48'200.00 CHF · −9'600.00 CHF against the plan" (green or red, "as in the plan" without a difference); its changes as rows ("Salary", "4'960.00 CHF every month · from Jan 2027 to the end of the plan", "No budget · Mar 2027", note) with edit and delete, *Add Change…* and, once it has a change, *Adopt into Plan…* (a confirmation names what happens, then the undoable toast "Adopted “…”: n budget entries"). The scenario sheet has name and note; the change sheet type (segmented), amount and currency, the category picker (F08) or the income category, From, Until (*Forever* = the end of the plan) and Repeat as in the New Budget Entry form, a note, and a caption "{n} months · The plan has 6'200.00 CHF for Jan 2027" (or "no budget for this category"). Deleting a scenario says how many changes go with it and that the plan doesn't change. |
| `/categories` | Categories | F37. All 602 COICOP 2018 categories (four levels) as one Quasar tree (`ui.tree`), built once: code and name in the interface language, divisions bold, the number of subcategories, and what was spent in each this calendar year (display currency, every level summed up). The search box matches code, name (interface language and English) and, since F40, the note words, every word ignoring case and accents, like the picker: Python searches `search_index()` (`coicop.match_words`), sets the tree's `filter` prop to the matching codes between spaces and a `:filter-method` keeps those nodes in the browser (Quasar keeps the ancestors of every match); the ancestors are put into `expanded`, so typing changes two props instead of rebuilding rows, and the nodes carry no search text. Clearing the search restores what was open before. `TAX` and `ADV` are not COICOP and appear only in a note below the tree. F40: a node with notes has `has_notes`; its info button emits `notes`, and the page adds the notes (`notes_view`: headings with items, "Unofficial translation" for a translation) to that node, or removes them, so only open nodes carry notes. A footnote names the UN source. |
| `/settings` | Settings | Grouped lists: interface language (system, English, Deutsch, Français, Italiano), plan horizon (Apply appears when changed), *Projected Wealth* (decision 36: *Budget only* or *Recorded where higher*, the default), display and base currency, accounts (with the asset class in the account sheet and the caption, F21; a securities or crypto account shows its value in holdings), advance-payment accounts (with outstanding balances), income categories. Add, edit and rename open small sheets. *Reminders* (F31): one switch per kind of reminder with what it lists; turning one off takes it off the Reminders page and out of the badge at once. *Data*: back up now (ZIP), restore from a backup file, export everything as JSON, the data folder's path, and *Sample Data* (F30): *Load Sample Data* into an empty folder, or *Remove Sample Data…* (a confirmation; every record, attachment and the history go, the settings stay, back to the welcome screen) when the folder holds the set; the sidebar footer says *Sample data* meanwhile; *Automatic Backups* lists the update and before-restore copies with a Restore action. *Updates* (F33): the *Check for Updates* switch (off by default, its caption says what is asked and that nothing is downloaded), and *Newest Release*: off, not checked yet, or "Checked on …: the newest release is …" with the releases page or the release itself linked when newer, and *Check Now* while on; turning it on checks at once, turning it off forgets the result. |
| `/welcome` | Welcome | F30. The first-start screen: the Overview opens it while no table holds a record and `onboarding_done` is not set (`demo.first_run`), so folders with records never see it; it stays reachable. No sidebar. The language (reloads the page), the display currency, the data folder with its path and how to change it (`--data-dir`, `BUDGET_DATA_DIR`), and two rows: *Open Settings* (sets `onboarding_done`) and *Load Sample Data* (disabled with a note while the folder has records; writes the set in a worker thread, then opens the Budget page). |
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
9. **Localhost only.** The server binds to `127.0.0.1` because the app has no login (F35 adds a second
   listener only while phones are allowed; see decision 41). Use
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
23. **A goal's reserve is what it is to hold on its plan; the projection stays as it is** (F14). Goals
    marked *reserve in the projection* make the Overview draw a second, dashed line beside the projection,
    *Free after goals*: projected wealth (F47, untouched) minus, at each projected month, what each such goal is
    to hold by then on its plan (`domain.goals.planned`): today's amount (the linked account's balance or the
    amount saved) plus the monthly amount needed for each month from now, capped at the target, and the full
    target from the month the plan reaches it (at the latest the target date's month) on. Without a date there
    is no plan, so it is what the goal holds now; an overdue or reached goal reserves its target. Amounts are
    converted at the latest rate, like budget entries. Weighed: subtracting the full target from now on (it
    counts money not saved yet, which the projection still has to earn) and subtracting it only from the target
    date (the line would ignore the saving on the way, then drop in one month). The plan variant is the honest
    one: saving for a goal is a transfer that leaves wealth unchanged, so free wealth grows by the expected net
    minus the goal's monthly amount. A goal's money is spent outside the plan (a holiday's spending is budgeted
    or booked like any other), so its reserve lasts until it is archived; the README says to archive a goal once
    its money is spent, or the spending and the reserve count it twice. Progress: a linked account's balance
    is its latest month-end balance plus what was booked on it after that month up to today (a securities or
    crypto account: its holdings), so a transfer to savings shows at once; *on track* compares it with the
    straight line from the start (the day the goal was added, or linked to another account, and what it held
    then) to the target on the target date. An emergency fund's target is N × the average recorded spending
    (own share: advances left out, taxes in) of the last 12 complete months, each transaction at its own rate,
    counting only months from the first transaction on, so a new data folder isn't diluted by empty months.
24. **A budget from history is proposed in CHF and replaces only the same category** (F16). The proposal reads
    each transaction's `amount_chf`, valued at its own date, rather than the display currency: a budget made from
    it is a CHF entry, its amounts don't move with today's rate or with the display setting, and rounding to 5 or
    10 CHF matches what a Swiss household plans in. Every basis month counts, an empty one as 0, so a cost that
    occurs every other month gets half of it per month; only the months before the first expenditure or income
    are left out, because they are no evidence of spending nothing. A conflict is resolved with the New Budget
    Entry form's own *Replace* (same type and category, the target months only), so the sheet introduces no
    second way to delete budgets. Budgets of related categories (above or inside the row's at another COICOP
    level) are not replaced, since they may plan something narrower or broader on purpose; they are named and
    the row starts unticked, as the roll-up would count both. Recurring series stay informational: their
    bookings are actual spending in the history, and the projection never uses series (F05), so a budget beside
    one counts once. Alternatives weighed: the display currency (proposals would change with the setting and the
    entries would be in a currency the history wasn't in), averaging only months with spending (overstates
    irregular costs), and leaving out categories a series books in (the user would lose the rent from the plan).
25. **The tax summary lists, it never judges; sections are categories plus tags** (F20). A section is defined by
    COICOP codes where COICOP has them and by tags the user links to it (`tax_sections.csv`), because COICOP knows no
    "deductible": commuting is passenger transport, but so is a holiday flight. Codes were chosen narrow rather than
    complete, and listed in the sheet: *professional expenses* 07.3 (itself), 07.3.1 rail, 07.3.2 (itself) and
    07.3.2.1 bus, 07.3.5 fare network passes, 11.1.2 (itself) and 11.1.2.9 canteens other than at school, 10.3–10.5
    post-secondary, tertiary and other education (courses) – not flights, taxis, restaurants or school fees;
    *insurance* 12.1 (itself), 12.1.1 life and accident, 12.1.2 health – not household, building, car or liability;
    *medical* 06 and the health services of non-profits and the state (14.2, 15.2); *donations* 14 (itself, the
    picker's "Donations and memberships"), 14.5 and 14.6 (charities, religion, parties, unions, environment) – not
    14.1/14.3/14.4, which are services bought; *childcare* 13.3.0.1; *debt interest* 12.2.1 (COICOP's interest margin
    of lenders and card issuers; interest itself is no consumption, so this section lives mostly from a tag).
    *Pillar 3a* is a rule on F21's asset classes (transfers into a 3a account from another class, so moves between
    3a accounts and withdrawals don't count); the *withholding tax* and the *securities list* come from the dividends
    (gross and tax as booked, so CHF at the transaction's rate), and income in a category named Dividends or Interest
    in any language. The code lists don't overlap, and a tag belongs to one section, so a transaction is in two
    sections only when its category matches one and a tag another; it is shown once per section with the sum of its
    matching parts (split lines in their own category, F11; the advance share left out), and the page and the PDF
    say that sections can overlap. Income tagged for a spending section counts negative (a refund from the health
    insurer lowers the medical costs); repayments of advances never count. Alternatives weighed: a "deductible" flag
    per transaction (a second, invisible categorization that the user would have to keep up) and tags only (every
    health-insurance premium tagged by hand, although its category says it all). The summary stores nothing but the
    links; the ZIP reads attachment files only through F12's check (decision 20), so a planted file never leaves
    the folder in it.
26. **Notable this month uses fixed, explained thresholds, in CHF, against the median of six months** (F18).
    The card must stay short and believable, so each rule needs both a relative and an absolute threshold (a
    category that triples from 20 to 60 CHF is not news) and the usual level is the **median** of the last six
    complete months, not the mean: one holiday or repair doesn't raise the bar for the next half year, and
    months before the first recorded expenditure don't count as zeros. Fewer than three such months give no
    comparison at all (budgets are still checked). Categories are compared per COICOP **group** (level 2): a
    division is too coarse to say what changed and a subclass too thin to have a usual level. A payment is
    judged by its own group when that has three or more earlier payments (four times their median), otherwise by
    the month (a quarter of a usual month), so the rent is never "large" and a first washing machine is. The
    pace projection is the plain spent ÷ day × days, guarded where it misleads: not before the 7th, and only for
    categories with three payments or more this month, since a bill paid once (rent, insurance) would otherwise
    project to many times its budget. Overlaps are removed rather than listed twice: of nested budgets the most
    specific, a large payment that explains half a category's excess instead of the category, an exceeded budget
    instead of the payments in it, and spending a budget covers is planned, not unusual. Thresholds are in CHF
    and converted to the display currency at the latest rate (like budget entries), so switching the currency
    never changes what is notable by more than the rounding of a rate. Alternatives weighed: user-set
    thresholds (one more settings page for a hint card; the constants sit together in `domain.trends` and can
    become settings later), standard deviations (meaningless with six samples) and the previous month alone
    (too noisy). The Budget page's comparison reuses the budget and difference columns rather than adding
    columns, so the ledgers keep the widths decision 1 gave them.
27. **The sample set is written into the data folder itself, only while it is empty, and removed as a whole**
    (F30). Weighed: a second data folder for the demo (the catalog's first idea). The app has one store, one
    single-instance lock, one backup marker and one FX file per folder, and `--data-dir` already opens any folder;
    switching folders at run time is what profiles (F34) add, so the welcome screen stays out of their way and uses
    `config.data_dir()` only to show where the data is. Loading goes through the services, so the set passes every
    validation and exercises every feature; it runs in a worker thread with the button in its loading state. The
    change history is cleared afterwards, since the sample records are no changes of the user's to undo, and the
    store is reloaded. The folder is marked in `settings.json` (`sample_data`); removing deletes the record files,
    the attachments and the history, keeps the settings (language, currency, horizon) and resets `onboarding_done`,
    so the first-start screen comes back. That screen appears only while no table holds a record and it was never
    passed, so a folder from an earlier version never shows it; it is a plain page without the sidebar, since the
    places mean nothing before the first record, and it offers no folder picker: choosing folders belongs to
    profiles. Without exchange rates the set is CHF only, so the first start works offline too.
28. **The update check is off until the user turns it on, reads one version number and never downloads**
    (F33). Cross-cutting decision 1 keeps everything local; the ECB rates are the one download the app makes, and
    an update check is the same kind of read-only request, so it is offered the same way: as a connection the user
    knows about, with the switch's caption saying what is sent (the request itself, with a plain user agent) and
    what is not (nothing about the user or the data; nothing is downloaded or installed). Off by default rather
    than on, because the installed app should make no connection the user didn't ask for; the README's privacy
    note names it. GitHub's *latest release* API is read, not the releases HTML page, so the answer is small and
    structured; it is capped at 1 MB, only a `tag_name` of the form `v1.2.3` is accepted, and the link is built
    from that tag, never taken from the answer, so a changed answer can at most name a version. Once a day,
    shared with the rate refresh; a failure is logged and retried at the next refresh, never shown, because a
    missed check matters little. The result is kept in `settings.json`, so every page's sidebar can show it
    without asking again; turning the switch off forgets it, so nothing the user didn't ask for stays on screen.
    A dev build (`app_version()` not a version) is never behind. Weighed: showing release notes in the app (a
    second, larger download) and offering the installer download (the installers are signed by nothing yet, and
    the update steps differ per OS; the release page has both).
29. **Receipts are read by a local engine shipped in the installers, and everything it reads is a proposal; the
    lines are offered only when they add up** (F04). Decision 1 of the catalog rules out online reading, so the
    question was which local engine installs from the lockfile alone on all three systems: Tesseract needs a
    binary or, through `tesserocr`'s wheels, macOS 15 and separately downloaded language files; EasyOCR and
    PaddleOCR pull PyTorch or Paddle (hundreds of MB) and download their models on first use; Apple Vision is
    macOS only. RapidOCR qualifies: a pure-Python wheel that carries its PP-OCRv6 detection, orientation and
    recognition models (Latin scripts with German, French and Italian included), run by ONNX Runtime, whose wheels
    cover macOS 14 on Apple silicon, Windows x64 and arm64 and Linux x86_64 and aarch64 for Python 3.13 (no Intel
    Mac any more). The two are the optional extra `ocr` and part of the `build` group, so `uv sync` stays small and
    the installers carry them (`--collect-submodules rapidocr --collect-data rapidocr` for the lazily imported
    engine module and the models, hooks exist for `cv2` and `onnxruntime`); the code imports them only when the
    first receipt is read and treats their absence as "type it yourself", never as an error. The engine's output is
    turned into a receipt by rules, not by learning: the last row naming a total (subtotal, tax, change and payment
    rows excluded), the first plausible date, CHF or EUR when named, the first row of words as merchant. These
    rules are deliberately simple and explained; the form shows what was read and the user checks it, as the catalog
    foresaw for local OCR. The item lines are the one place where a wrong guess would spread (a split counts in
    reports and budgets), so they are filled in only when the lines add up to the total, discounts included.
    A PDF with a text layer (an e-receipt) is read from that text, which is exact. The engine loads in about ten
    seconds once and reads a receipt in one to two; both run in a worker thread (ONNX Runtime releases the GIL)
    while PDF pages are rendered in the event loop, as for QR bills, because PDFium is not thread-safe. The receipt
    sheet is a sheet on the Transactions page and not a page of its own: a receipt is one transaction, there is no
    list to keep (a QR bill waits to be paid, a receipt is paid), and the picture beside the form is what makes
    typing the rest easy where the engine is missing or wrong. Alternatives weighed: a drop zone in the
    transaction form itself (the picture would have to fit under the form), bundling language files for Tesseract
    in the repository (16 MB of binaries, and no macOS 14), and an optional download of models (decision 1 and
    F33's "off by default" argue against any network the user did not ask for).
30. **A scenario sets budgets, it never adds to them; adopting replaces and removes the scenario** (F15,
    decision 18 of the catalog). A change says what a category's budget *is* in its months – "set" semantics only,
    with 0 meaning "no budget", because that is exactly what *Adopt into Plan* can write with the New Budget Entry
    form's own *Replace* (same type and category, the target months only; decision 24 chose the same path for F16):
    the overlay the chart shows and the plan after adopting are the same by construction, and no second kind of
    budget entry is needed. Weighed and rejected: "add" changes on top of the plan (an adopted plan would hold two
    entries for one category and month, and a change could no longer be read as the resulting budget) and changes
    to recorded transactions or balances (a scenario is about the future; the past is what it is). The projection
    keeps F47's rule with the scenario's budgets in place of the plan's, so recorded overspending still counts and
    a scenario can never look better than the facts. Where two changes of a scenario meet, the later one wins, in
    the overlay and when adopting (the later series is created later, with Replace); no refusal, because a general
    cut from January and a bonus month in July are a natural pair. Adopting removes the scenario in the same action:
    once its changes are the plan, its line would only repeat the plan's, and Undo restores both. Every change is
    checked before anything is written (the plan may have shrunk since a change was made), as in F16. Scenarios are
    drawn from the last recorded value like the projection, with the plan's colour family left to the plan and the
    goals' line unchanged: a scenario changes budgets, not what the goals are to hold.
31. **Reminders are computed, in the app, from what the features already know; nothing is stored or sent**
    (F31). The catalog asked for desktop notifications, optional, and for reminders while the app is closed as a
    larger step. Both were left out: a system notification needs a platform layer per OS (and in the browser a
    permission prompt), and a background helper would be a second process holding the data folder, against the
    single-instance lock (`storage/lock.py`); neither fits "nothing leaves the computer unless the user turns it on"
    better than a page that is simply there when the app opens. So a reminder is a *view*: each kind reuses the
    service that already decides the matter (the Budget page's missing-balance rule, the Due list, the bills due
    within a week, F18's exceeded and pace items with their thresholds, F14's statuses), so the page can never
    disagree with the place it links to, and there is no second set of thresholds to keep in step. Nothing is
    stored per reminder (no "seen", no snooze): a reminder goes away when its matter is dealt with, which is the
    honest state, and a kind the user doesn't want is switched off in Settings (`reminders_off`, a setting rather
    than a record, so it is not part of the undo history and not in backups' records – it is a preference). The
    sidebar badge counts the reminders like Recurring's and Bills' badges count theirs; it shows the display
    currency's figures. Desktop notifications can be added later as one more way to show the same list.
32. **The year in review is the twelve statements added up; the current year is reviewed so far against the
    same months** (F19). Every figure comes from the same sums as the Budget page (`aggregates`, per part, F11), so
    the year agrees with its months and a split line, an advance or a repayment counts exactly as it does there:
    advances stay a row of their own (*Advance payments*) and their repayments income, which keeps the net equal to
    the reconciliation's; nothing is netted or dropped. The current year is January to the current month and is
    compared with the same months of the year before, not the whole of it, since October against twelve months
    says nothing; its wealth is the end of the last complete month, because balances are month-end (and in January
    there is none yet). Wealth is recorded wealth only, never the projection: the review reports, the Overview
    plans. The savings rate is (income − expenditure) / income and is not shown without income. The biggest
    payments are ranked by their own share (a dinner half paid for a friend is half a dinner) and a split receipt
    counts whole, since the payment is what was spent at once. The page lists the statement's top-level rows
    (divisions, Taxes, Advance payments) rather than groups: a year has enough rows at level 1 and the Budget page
    has the detail. Changes reuse F18's `Change` and percentage, so ▲/▼ mean the same on both pages. The PDF takes
    the chart from the browser like the Overview's *Save as PDF* (decision 7: no headless renderer) and works
    without it. Alternatives weighed: a calendar-year-only review (useless until January), the mean of the months
    (the totals say it), and goals, tags or accounts per year (the Goals, Tags and Investments pages have them).
33. **A subscription is a rhythm plus similar amounts, found per payee; the page suggests and never books** (F17).
    Charges are grouped by the payee's lookup key (F09; a row without a payee by its folded note), so the detection
    rests on the same identity imports and renames keep, and by currency, since amounts are compared as charged. Only
    a group's latest run counts: going back from the last charge, a charge belongs to it while its gap fits the period
    of the last gap (one of five, monthly to yearly, within 4 days plus one per month, since month ends and weekends
    move a charge by a few days) and the amounts stay within 20 % of the median, or within 2.00 for small ones, so a
    9.90 that became 11.90 still counts while a grocer's varying receipts never do. A skipped or late charge, an
    irregular one or an old price ends the run, so years of imported history with one late payment, a pause or a price
    rise still find today's subscription, described by its run alone (count, first charge, usual amount). Three
    charges are needed (two for half-yearly and yearly rhythms, which would otherwise take over a year to appear), and
    a run whose last charge is older than two rhythms has stopped and is not listed. The thresholds are fixed and
    explained in `domain.subscriptions`, like F18's (decision 26); they can become settings later. Rows a series
    booked never count and a group whose payee already has a running expenditure series is left out, so making a
    subscription recurring moves it from *Detected* to *Recurring Transactions* instead of listing it twice; the user
    can also *Ignore* a group, which is stored (`subscription_ignores.csv`) and undoable, because the hairdresser
    every four weeks would otherwise be suggested forever. *Make Recurring…* reuses F05's sheet with the last charge
    as the series' first booking, the way *Make Recurring* on the Transactions page does, so the next occurrence falls
    a rhythm later and nothing is booked twice; the page itself books nothing (decision 16's spirit: rules suggest,
    never apply). Alternatives weighed: detection by category (a category holds many payees), requiring the whole
    history to keep one rhythm (one late payment in years hides a subscription for good), tolerating a skipped charge
    (hides a cancelled subscription), amounts within a fixed franc band only (wrong for both 5 and 500) and a yearly
    total in the charges' currency only (the header is in the display currency so the sum means something; each row
    keeps its own).
34. **A loan is a liability account valued from its bookings; its interest is spending in 12.2.1, its
    payments are booked like recurring transactions** (F22). The account's balance is minus the outstanding debt
    and is never recorded (like a securities account's value, decision 17): nothing can fall out of step, undo
    needs nothing extra, and every month after the first reconciles by construction. The principal is the debt on
    the start date and not a transaction: a mortgage's payout goes to the seller, who is not in the app; a loan
    paid into one of your accounts is recorded as a transfer from the loan account with a principal of 0, so the
    debt comes from the transfer. The payout therefore shows as the account's unexplained change in its start
    month, as a newly recorded account's first balance does. Interest is booked as expenditure in the COICOP code
    12.2.1 (*Financial intermediation services indirectly measured*, the lender's margin): COICOP treats interest
    itself as no consumption and the app has no pseudo-category for it besides *Taxes*, and 12.2.1 is already the
    code the tax summary (decision 25) counts as debt interest, so mortgage interest lands in the tax summary
    without a tag; the category is a setting per loan. Interest is simple interest per period on the debt before
    the due date (30/360, what Swiss banks quote); because banks round differently and dates shift, the payments
    are proposed and booked like recurring transactions (decision 14) with the amounts editable, not rebooked
    from the schedule like trades (decision 17): the booked rows stay ordinary transactions carrying `loan_id`.
    There is no automatic booking (an interest amount deserves a look) and the projection is left alone: the
    repayment moves money between two accounts, and the interest is budgeted like any other cost, so a budget
    beside a loan counts once, as with recurring transactions. Alternatives weighed: recorded balances for the
    loan account (the schedule would be informational and the debt could drift from the payments), a pseudo-
    category *Interest* beside *Taxes* (one more special case in every report for one kind of cost) and
    rebooking from the schedule (every rounding difference would need the schedule edited).
35. **Who owes what is read from the advance payments; settling up records repayments, oldest first**
    (F23). An advance already holds the share paid for someone and their name, and a repayment the money that came
    back, so the page adds no table, no field and no transaction type: a person is the debtor folded like a payee
    (case and accents ignored), shown as last written, and a repayment counts for the debtor of the advance it
    settles, so correcting a debtor's name moves what they repaid along. The settle-up is a **repayment, not a
    transfer**: decision 1 keeps advances outside wealth and counts repayments as income, and a transfer from the
    advance account would bypass that and the rules of decisions 6 and 12. One amount received is spread over the
    person's open items in that currency **oldest first**, the last one partly, as one repayment per item and one
    undoable action, and refused above what is open; a person who owes in two currencies settles them one at a
    time, because a repayment is in the currency of its advance (decision 6). Totals stay per currency rather than
    converted to CHF, since what is owed is an amount in that currency; *this year* is the calendar year of the
    repayment's date. The summary is text and a one-page PDF to copy or save; nothing is sent (ground rules). An
    advance keeps one debtor: a payment for several people is recorded once per person, or with the people named
    together. Alternatives weighed: a persons table (one more list to keep in step with the debtor field, for no
    figure it would add), a settle-up as a transfer between accounts (it would leave the advance open) and picking
    the items to settle by hand (the oldest-first rule is what people expect and the caption shows it; a single
    item can still be repaid from the transaction form).
36. **The projection's rule is the user's choice; every rate is applied again at each start** (change of the
    product owner, 2026-10-29). A projected month (one after the last recorded balances, decision 5 of the catalog)
    adds either its budgets alone (option 1, *Budget only*) or, per top-level row, the recorded amount where it
    exceeds the budget (option 2, *Recorded where higher*, F47's rule). Option 2 stays the default, so the
    projection of an existing folder doesn't change with the update; option 1 suits a plan read as a target, where
    what has been spent so far says nothing about the month's end. Option 1 sums the same top-level rows as option
    2, so a budget set on a parent and on its child counts the same in both, and advances (no budget) count
    nothing. The choice is a setting, not a record (like decision 31's `reminders_off`): not undoable, not part of
    the change history. It applies to every projected month, finished ones without balances included, because
    the owner's "month not yet fully passed" is F47's wording that decision 5 extended; and to the scenarios'
    lines (F15), which share `aggregates.net_with_budgets`. Values in CHF: after each rate check, at the start and
    every 30 minutes, every transaction and every account entry is valued again at its rate (the transaction's
    date, the account entry's month end) and stored where the CHF amount or the rate date differ. Comparing the
    recomputed amount, and not only the rate date as before, also repairs rows whose amount doesn't match their
    date's rate; account entries were never revalued before, so a balance of the running month recorded early kept
    an older rate. Budget entries keep the latest rate when saved (§4.1), outside the owner's change.
37. **Keys are single letters outside fields; sheets keep theirs; contrast is a test, not a review** (F32).
    Page keys (`N`, `/`, `←` `→`, `1`–`9`, `,`, `?`) take no modifier, so they never clash with the browser's or the
    system's shortcuts, and `ui.keyboard` ignores them while the cursor is in a field; they also wait while a sheet
    is open, so a digit never leaves a half-filled sheet. Pages switch by the digit of their place in the sidebar
    rather than by mnemonic letters: the sidebar order is visible, survives translation (letters would differ per
    language) and grows with new pages; places after the ninth are reached with Tab. A sheet's Ctrl/⌘ Enter is a
    keydown handler on the sheet's own card that stops the event there, so a sheet opened from a form never also
    triggers the form's document-wide shortcut (the template sheet used to save the transaction as well). Screen
    reader names reuse the tooltip text (`layout.named`) so there is one string to translate. Contrast is checked
    against WCAG AA for normal text (4.5:1) on all three surfaces in both appearances, computed from the tokens in
    `theme.CSS`; where one colour cannot serve as both text on dark surfaces and the fill under white text (the dark
    accent), filled buttons get their own token (`--accent-strong`, `--negative-strong`). Tag chip colours are
    outside the check: their text sits on the tag's own shade and is decorative next to the name.
38. **The lock is a password; encryption is a key the password and a recovery key wrap** (F27,
    decision 20). The lock is a session cookie granted after the password, not a check on the process: the server
    also answers phones once F35 is on, so each browser proves itself, and every start of the app locks again
    because sessions live in memory only. The guard runs before a page is built (`main._guarded` returns a
    redirect), since a page's HTML carries the records; the attachment route checks the same session. Pages set
    no cookie after they are sent, hence the one-time nonce and the `/unlock/<nonce>` route. The password is
    stretched with scrypt (`cryptography`; parameters stored per entry so they can be raised) into a verifier
    that is stored and a wrapping key that is not. Encryption uses one random data key, so changing the password
    rewraps a few bytes instead of rewriting the folder, and the recovery key (160 bits, base32 in groups of
    four, shown once) is a second wrapping of the same key: no secret is ever written in the clear, and a
    forgotten password without the recovery key means the records are gone, which Settings says. Fernet (AES-CBC
    with HMAC, IV and timestamp in the token) was chosen over a hand-assembled AES-GCM because it is the
    library's own "recipe" layer; nothing cryptographic is implemented in this app. Files keep their names and
    gain a marker line, so backups, restore, the version copies and the file list code are unchanged; the codec
    reads plain files even with a key, so a folder may be mixed while encryption is turned on or off, right after
    a restore, or after a crash in between, and `reencode` finishes the job. *Back Up Now* writes plain text on
    purpose: a backup is for a disk that died, when the `security.json` is gone with it, and it must restore
    without anything but itself; the Settings caption says so. The app's own copies in `backups/` are inside the
    data folder and are encrypted or decrypted with it, so a `before-restore` copy stays restorable. A backup
    is refused if it carries `security.json`, so a crafted archive cannot replace the key material. An
    encrypted folder cannot be read at startup, so the store opens after the unlock (`security.opener`), and
    the rate refresh and automatic bookings follow then. Wrong passwords are answered more slowly from the fourth
    try (1, 2, 4 … 30 s, process-wide): the lock also faces the home network once F35 is on. Touch ID and Windows
    Hello were left out: no vetted library covers all three systems from the lockfile. Attachment files are not
    encrypted; they are served and opened as files, and the README says so.
39. **A profile is a data folder; the registry is the only thing outside it** (F34). Each profile keeps its
    records, settings, lock and encryption in its own folder, so a profile is complete wherever it lies (a cloud
    folder, F26, another disk), can be opened with `--data-dir` alone, and backups, restore and the version copies
    work per folder unchanged. The registry `profiles.json` sits in the home folder, which stays the first
    profile's folder, so existing installations need no migration and `--data-dir` still names one folder: the
    home folder whose registry decides what opens (`config.data_dir()`). The app has one open folder per process
    (decision 13's single-instance lock, the process-wide context and security state), so switching closes one
    folder and opens the other rather than opening a second window; the lock on the new folder is taken before the
    old one is released, so a folder another window has open is refused without anything changing. A switch
    starts every open page over: a page holds its context, and a stale page writing into the other folder is
    worse than a reload. Each profile has its own lock, which is why the lock page may switch to another profile:
    it does not open the locked one, and an unlocked profile is readable on disk anyway. *Remove* forgets a profile
    and never deletes a folder: deleting records is a file-manager action the app does not need. Profile folders
    without a typed path go to `<home>/profiles/<name>`, not directly into the home folder, where they would sit
    beside `attachments/` and `backups/` of the first profile.
40. **A cloud folder is used one computer at a time: claims warn, changed files are read again, nothing is
    merged** (F26, decision 19). The app never talks to iCloud, OneDrive or Dropbox; their sync clients copy the
    folder, so the feature stays local and needs no account. Moving copies instead of moving, and leaves the old
    folder alone: a cloud folder that turns out to be wrong costs nothing, and deleting records is the file
    manager's job. The OS lock (decision 13) holds on one computer only, so each computer also writes a claim
    file of its own: one file per computer, because sync clients merge two files written on two computers well
    and one file written on both badly (conflict copies). A claim is a warning, not a lock: a computer that
    crashed or sleeps must not lock the other one out, so a claim counts for ten minutes after its last
    heartbeat, and both computers show the warning while both are open. Change detection compares the size and
    change time with what the app itself last read or wrote (every record file passes `encryption.read_text` /
    `write_text`, so no hashing and no second copy), every five seconds; a change is read only once it is the same
    on two checks, because a sync client writes an action's files one by one, and the store is read again in
    place so pages holding it see the new records. Merging per record (F28's history would allow it) was left out
    by decision 19: it needs an answer for every conflict, and one computer at a time needs none. Each computer
    keeps its own profile list, since the paths differ; the second computer adds the cloud folder as a profile.
41. **Phones reach the app through a second listener that exists only while phones are allowed, behind a
    gate, the lock and a pairing code** (F35, decision 21; decision 9). The app's own server keeps binding to
    `127.0.0.1`, so with phones off nothing on the network can connect, exactly as before. Turning *Quick Add from a
    Phone* on requires the lock (F27) and a confirmation, and starts a second uvicorn server for the same app on all
    interfaces, port 8484 (a free one when taken); turning it off, turning the lock off, switching to a profile
    without it and quitting stop it (`security.on_phone_change` → `phone.sync_listener`). Three checks stand
    between another device and the records: the `Gate` middleware runs before every route on both servers and lets
    a non-local client reach only `/pair`, `/quick`, the cookie route and NiceGUI's own files and socket, only from a
    private address (RFC 1918, link-local, ULA; "no entries from away from home"), and only while phones are
    allowed; the page guard then wants a phone session for `/quick`; and that session only comes from the six-digit
    pairing code shown in Settings on the computer (once, ten minutes, five tries, compared in constant time, from
    `secrets`). A phone session never opens another page, and the lock page and the password are never offered to
    other devices, so a phone can't reach the desktop session even with the password. The server is not bound to the
    home address alone because addresses change (DHCP, Wi-Fi and cable); the gate checks the client instead. Plain
    HTTP, not HTTPS: a self-signed certificate (which `cryptography` could make) makes phone browsers refuse or warn
    on every visit, and a home network is the boundary decision 21 chose; the confirmation and the README say the
    connection is not encrypted. What a phone sees is minimal (the account and category names to choose from, and
    what it saved itself), and what it can do is add an expense, which the history (F28) can undo. Phone sessions
    live in memory like the desktop's, so a phone pairs again after each start. Starting the app with `--host` on
    another interface is the user's explicit choice and keeps its old meaning (`security.trust_network`: every client
    counts as this computer, the lock's password still applies). The phone app with an offline queue (the catalog's
    larger step) is not built.

42. **Seven places, with tabs for the pages of a place** (UX review before 1.0, `docs/UX_REVIEW.md`). The sidebar
    had grown to eighteen places, one per feature, and several answered the same question in different ways. A place
    now stands for one question (where am I, this month, what happened, what's planned, what do I own, how was the
    year), and its pages are tabs under the title, so a feature added later joins a place instead of the sidebar.
    The routes stay, so links, shortcuts and tests keep working, and a page that is no tab (Import, Rules) highlights
    the tab it belongs to. Accounts left Settings: they are records, not preferences, and the first thing a new
    user adds, so the welcome screen opens them; income categories joined the COICOP categories, and Settings keeps
    preferences only. The sidebar keeps one badge, the Overview's count of what is due; the other counts moved to
    their tabs, so a number in the sidebar always means "something to do". Pages share one column so the title stays
    in place; narrow lists stay readable at 46 rem, left-aligned rather than centred, so the eye doesn't jump.
    Alternatives weighed: collapsible groups in the sidebar (still eighteen entries to scan, and a click to open a
    group), and a command palette (useful, but a search box doesn't tell a new user what the app has).

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
  - budget from history (F16): basis months with and without a later start, COICOP levels, average and median
    (even counts, empty months as 0), rounding steps half up and never below 0, split lines, advance shares and
    repayments left out, CHF at each transaction's rate in any display currency, transfers and unknown or deleted
    categories left out, conflicts in the target months only, overlaps above and inside, recurring series
    (ended ones not), an invalid period, the order of rows, creating with and without replace in one undo step,
    all or nothing; the sheet in the simulated client (inputs, typed amounts, conflicts, flags, short and empty
    histories, creating, cancelling, errors);
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
  - savings goals (F14): whole months and the monthly amount needed (rounded up, past and today's dates), on track and behind, reached, overdue and open-ended goals, the planned reserve and free wealth with the projection unchanged, an emergency fund's target (window, own share, taxes, months before the data), an account's balance today (bookings after the last balance, exchanges, a securities account), conversion at the latest rate, validation, the line restarting with another account or currency, archive, restore and delete with undo, `goals.csv` and the JSON export; the Goals page, the Overview card and the *Free after goals* line in the simulated client;
  - what-if scenarios (F15): names tidied and unique, shown and hidden, delete with the changes and undo, the JSON
    export and `scenarios.csv`; a change's checks (amount, currency, category, months as the budget form), its months
    to the end of the plan or with a rhythm, after the plan shrank; the overlay replacing the plan only where the
    scenario says, 0 taking a budget out, the later change winning, another view currency, recorded amounts above
    the scenario's budget still counting, the projection and the outcome against the plan, a scenario without
    changes being the plan; adopting with Replace in entry order, 0 removing entries, the scenario removed, one undo
    step restoring plan and scenario, nothing written when a change lies beyond the plan's end; the page, the sheets,
    the switch, adopt and the Overview's scenario lines and export in the simulated client;
  - reminders (F31): an empty folder, balances missing for last month (not after a zero balance), due recurring
    occurrences with their dates and tones, bills overdue, due today and within a week (not later or undated),
    budgets exceeded and on pace with their links and another view currency, goals behind and overdue (not on track
    or open-ended), kinds turned off and the setting saved and reloaded, the count; the page with its groups, links
    and badge, a data change refreshing it, and the switches in Settings in the simulated client;
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
  - receipt scanning (F04): rows from boxes (one row from boxes within half a height, anchored on the first),
    amounts in Swiss and European formats with trailing minus (dates and times never), dates in five formats
    (future and pre-2000 refused), currencies, a Swiss and a French receipt, the total in seven languages' words,
    subtotals and VAT skipped, the last total row, the largest amount as fallback, lines that add up (with a
    discount) and lines that don't, quantities dropped from names, the merchant row, an empty receipt; the engine
    boundary with the engine stood in for (availability by packages and model files, loading once, failure logged
    once, RapidOCR's output converted); pictures from JPEG, PNG and PDFs (RGB, three pages at most, the preview
    scaled to 1200 px, a text layer read without the engine), refused types and sizes, an injected engine, no
    engine, an engine failure; the prefill with a known payee, a new merchant, lines and no total; the Receipt sheet
    in the simulated client (read, shown, saved with lines and file; without the engine; nothing read; an engine
    failure; refused files; cancel);
  - tax summary (F20): the sections' code lists not overlapping and naming real categories, which section 23
    categories belong to, categories in CHF by year, split lines in their own category and advances left out,
    tags on transactions, lines, income (refunds negative) and transfers, overlapping sections, repayments and
    exchanges never counting, pillar 3a transfers (not between 3a accounts, not withdrawals), dividends in CHF and
    EUR with the reclaimable tax, income categories named in another language, linking tags (new names, one
    section per tag, one undo step, tags deleted and merged), the CSV (formula-safe), the PDF's text, the ZIP's
    folders and names, files that fail the check left out, the size limit; the page in the simulated client
    (sections, year, the sheet, opening a row and its proof, the three exports);
  - wealth with carry-forward, projected wealth (higher of recorded and budget) and the reconciliation;
  - sample data (F30): the first-run flag (an empty folder, passed, a folder with records), loading (every table,
    attachments, the empty history, the flags, CHF only without rates, refused on a folder with records), removing
    (empty again, settings kept, flags reset, refused without the set); the welcome screen in the simulated client
    (opened on the first start only, no sidebar, language and currency saved, both ways in, a failed load reported,
    the Settings row loading and removing with its confirmation);
  - update check (F33): version parsing and comparison (dev builds, short versions), the request through
    `httpx.MockTransport` (never the network: the tag read, refused answers – 404, no JSON, a tag that isn't a
    version, a list, bad UTF-8, over 1 MB – and a network failure), off by default makes no request, once a day,
    the next day and *Check Now*, the result stored and shown, a current app with nothing to link, a failed check
    keeping the last result and logged, turning off forgetting it; in the pages the switch and its note, turning
    on checking at once, the sidebar link on every page, *Check Now*, a current app and a failed check;
  - year in review (F19): the period of a past year, the current year so far and a later year, the savings rate and
    share, the years offered and the default, a year's totals and changes against the year before (split lines,
    an advance and its repayment, the rows in COICOP order, new and stopped rows, the biggest payments by own
    share with ties, the months, wealth and the year before's change, a year without balances before it, an empty
    year), the current year against the same months (a balance of the running month ignored, January), another
    view currency at each transaction's rate, the labels, the frames and the Excel file, the PDF with and without
    the chart and of an empty year; the page in the simulated client (the sidebar entry, the metrics, captions and
    changes, the chart, year and currency, opening a payment and refreshing, exports and their errors);
  - subscriptions (F17): the period of a gap at every boundary, the latest run following month ends and starting
    after a skipped month or an old price, the charges needed per period, similar amounts (20 % and the 2.00 floor),
    the next charge and when a run has stopped, detection with charges of a day summed, too few, stopped, erratic and
    late runs, a yearly one; older history left out of a run's facts; grouping by payee key or note with booked rows
    left out, the facts of a detected subscription and the order, the last spelling and the most frequent category,
    running series and ignored groups left out (not an ended series), ignore and watch again with undo, another
    currency at the latest rate, the series prefilled from the last charge (and the group gone once it exists), the
    running series' yearly cost, the table and the JSON export; the page in the simulated client (the sidebar entry,
    the empty page, captions and totals, Make Recurring through the sheet, Ignore and Watch Again, stale rows, the
    series group);
  - trends (F18): changes and percentages, each threshold on both sides, the month before and a year before
    (split lines and income only in the compared month, another view currency), and *Notable this month*:
    nothing recorded, a short history, a quiet month, a category above its usual level and a small one that
    isn't, spending a budget covers, a large payment beside the rent, split lines and an advance, exceeded
    budgets and the pace (not for a bill paid once), nested budgets, ordering and the limit of five, and
    thresholds in EUR; in the pages, the comparison at work, the links with their filters, and the card's
    states;
  - split transactions (F11): the parts adding up exactly in cents and micro-CHF (also 400 random receipts with
    discounts, tags and advances), foreign currencies at the transaction's rate, the CHF rounding never left on
    an empty rest, discounts, lines above the amount refused, line categories kept only on expenditure, old
    item files without the columns, the statement, budget vs. actual, the projection, chart series, the
    COICOP page and tag totals counting the parts, a split never changing any month's net, balances,
    unexplained difference or projection (CHF, EUR, USD views), advances shared proportionally with repayment,
    settlement and the edit lock unchanged, tags deleted and merged on lines, bulk edit changing only the rest,
    and undo of a transaction with its lines;
  - loans and mortgages (F22): interest half up for a period, payment dates, schedules with the last payment taking
    what is left, interest-only schedules stopping at a limit or a date, the share repaid; adding a loan with its
    account in one undo step, 16 refused inputs, the debt from repayments and drawdowns, a payout from zero, the
    account's balance (none before the start, minus the debt after, refused as a recorded balance, in wealth and by
    class, the first recorded month), a paid-off loan, due payments with the interest on the debt before each,
    booking with the schedule's or other amounts and date, refused bookings (order, above the debt, negative),
    skipping, the last payment, *Book All* leaving a failing loan due, the status and renewals, editing (schedule
    continuing after the handled payments, currency locked), deleting (refused with transactions, with the account,
    undone), the account's class only through a loan, booked rows keeping their loan through edits, the month of a
    payment reconciling in CHF and EUR, the CSV round trip and the JSON export; the page in the simulated client
    (figures, the details sheet, book, skip, book all, stale rows, the loan sheet with its preview, edit, delete,
    the sidebar badge, Settings, Budget and Overview);
  - who owes what (F23): people from the advances (folded names, the spelling last used, oldest open item first,
    open and settled this year per currency, advances without a debtor left out, repayments following the debtor
    of their advance), the settle-up plan (oldest first, partly, the cent tolerance, refused above the open amount,
    without an amount or in a currency not owed), repayments booked per item into the chosen account with the note
    or a default one, one undo step, an account in another currency refused with nothing saved, the summary as
    text and PDF and its file name; the page in the simulated client (the empty page and its place in the sidebar,
    rows and their order, folding the items out and opening one, the settle-up sheet in one and two currencies with
    its preview and errors, the summary sheet with copy, PDF and text, a refresh after a change elsewhere);
  - app lock and encryption (F27): the codec (plain and encrypted round trips, a tampered file, the wrong key,
    reading plain files with a key, re-encoding a folder's record files and nothing else), the vault (short and
    repeated passwords, wrong ones, change and removal, recovery keys and their normalization, one data key
    behind password and recovery key, reset, a retired recovery key, turning encryption off), an encrypted store
    and its JSON files, backups that stay plain and restore into an encrypted folder, the app's own copies read
    through the key, a backup carrying `security.json` refused, sessions through one-time nonces, safe next
    paths, slowed wrong attempts, stale nonces, re-encoding the folder and its copies, the store opening after
    the unlock, the attachment route behind the lock; in the simulated client the lock page (redirect, wrong and
    right password, the cookie holding across pages, Lock Now, the recovery key) and the Security group in
    Settings (set, change and turn off the password, encryption on with the recovery key shown once, a new
    recovery key, off again);
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

Installed builds are made with PyInstaller (see [packaging/README.md](../packaging/README.md)), by hand on
each system, and attached to the GitHub release of their version with a `.sha256` file each:

- **macOS:** `Budget.app` inside a drag-to-Applications `.dmg`.
- **Windows:** an Inno Setup installer. It installs per user, needs no admin rights, and
  adds a Start-menu entry and an uninstaller.
- **Linux:** a `.tar.gz` with the app folder and a per-user `install.sh` (Qt WebEngine window).

An installed build:

- bundles Python and all dependencies, so neither uv nor Python is needed;
- opens in a native window (pywebview: WebKit on macOS, WebView2 on Windows) on a free port;
- keeps its data in the user data folder:
  - macOS: `~/Library/Application Support/Budget`
  - Windows: `%APPDATA%\Budget`

  The data survives updates and uninstalls. `BUDGET_DATA_DIR` or `--data-dir` override the
  location.
