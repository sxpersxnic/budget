# Design review

A critique of the first UI and the redesign on branch `feat/design-review`. The review uses
the standard of Apple's Human Interface team: clarity, deference to content, restraint, one
clear hierarchy per screen. The domain logic did not change. Every requirement in
`Description_Budgeting_App.md` still holds.

## Verdict on the first version

It worked, and it looked like an admin panel. Every screen gave every element the same
weight, so nothing led. The app showed its framework (Material, upper-case buttons,
drop shadows on everything) more than it showed the user's money.

### Structure

- **Verbs in the navigation.** Seven top-bar items mixed places ("Transactions") with actions
  ("Add transaction", "Add budget entry"). An action is not a destination. Recording a
  transaction, the most frequent task, cost a page change and lost the user's context.
- **Paired pages for one object.** "Add budget entry" and "Budget entries" were two places
  for one thing, and so were "Add transaction" and "Transactions".
- **Seven equal tabs, no grouping.** Settings sat in the same row as daily work.

### Hierarchy

- **KPI cards repeated the tables.** The Budget page showed Income and Expenditure as cards,
  then again as table totals right below.
- **All values got equal emphasis.** "Current wealth", the number the app exists to show,
  had the same size and weight as "Projected change".
- **Thirteen rows of `0.00`.** The expenditure table gave empty COICOP divisions the same
  ink as real spending. The eye had to filter out the zeros to find the money.
- **Long category names wrapped to three lines** and broke the table's rhythm.
- **Every row had a pencil icon.** Seventeen identical controls drowned out the numbers.

### Controls

- **Months need a stepper.** The month was a dropdown, yet the most common move is
  "previous month".
- **The transaction type came after the amount.** The type decides every other field, so it
  has to come first.
- **The form scolded before the user typed.** "Amount must be a positive number" appeared
  next to a disabled Save button on an empty form.
- **Checkboxes for modes.** "Advance payment" switches the form into a different mode, which
  is a switch's job.
- **Edit and Delete were always present** as large filled buttons, red included, even with
  nothing selected.
- **A floppy-disk icon on Save** in 2026.
- **Four separate download buttons per chart** (PNG, CSV, Excel, close) competed with the
  chart title.

### Visual language

- Upper-case button labels, Roboto, Material drop shadows and a slate-blue palette with no
  meaning attached to colour.
- **Colour meant nothing in the charts.** Income was orange and spending blue. Projected
  wealth switched hue (orange) even though it continues the same series.
- **Duplicate titles.** Each chart repeated its card title inside the plot, next to a Plotly
  toolbar.
- **No dark mode** for an app that runs in a native window beside other macOS apps.
- **On a phone, the navigation took four rows** of the screen before any content appeared.

## What changed

### Structure

- A **sidebar** with four places (Overview, Budget, Transactions, Budget Plan) plus Settings
  pinned at the bottom. Exchange-rate freshness moved there as quiet footer text.
- **New Transaction is a sheet**, available from every page through the sidebar button and
  the phone header's `+`. The page behind refreshes when it closes (`layout.on_data_changed`).
  New Budget Entry is a sheet on Budget Plan. The old routes still work.
- Page actions sit beside the large title, so each page has one toolbar.

### Budget page

- A **‹ month › stepper** with a menu for jumping.
- A **metric strip** shows three wealth numbers with "current wealth" as the hero. Income and
  expenditure live only in their ledgers.
- **Income and expenditure share one ledger layout:** category, budget meter, actual, budget,
  difference. Each ledger's header states its total in plain words, for example
  "181.03 left of 3'500.00 CHF".
- **Zero values show as a dash** and empty rows are dimmed. All divisions still appear, as
  the specification requires.
- COICOP codes are set in a lighter colour so the names read first. Long names truncate, and
  the full name appears on hover.
- The edit pencil appears on row hover (always visible on touch screens).
- A **callout** names accounts without a balance for a past month, with a Record button.
- Statement export moved into a share menu.

### Forms

- The type is a **segmented control** at the top, and the amount is the largest field.
- A single column that works as both page and sheet.
- **Switches** for advance payment and repayment. Their dependent fields sit indented beneath.
- Validation explains itself only after the user has started typing. The buttons say what
  they do: "Add Transaction", "Save Changes", "Add to Budget".

### Lists

- A **search field** comes first, and the detailed filters sit behind "Filters".
- **Selecting rows reveals** an Edit / Delete bar.
- Amounts show their currency, and income is signed and green, like a bank statement.
  Dates read "9 Oct 2026" but still sort and filter on ISO values.
- A row count reads "40 of 171 transactions" when filters hide rows.

### Settings

- **Grouped lists** in the style of macOS System Settings. Row actions appear on hover.
  "Add Account…" rows open small sheets. The inline text fields with check-mark buttons are
  gone.
- The horizon's **Apply button appears only when the value changed**.

### Visual system (`ui/theme.py`)

- Every colour is a CSS token with a dark variant. The app follows the system appearance,
  and Quasar, AG Grid and the custom components all switch together.
- The system font stack (SF Pro on macOS, Segoe UI on Windows), tabular figures for every
  number, and title-case labels without upper case.
- **Charts:** income green, spending blue, over-budget red. The projection continues the same
  colour as a dotted line. Charts have no inner titles and no toolbar, a hairline grid, Swiss
  thousands separators and right-hand axis labels. The wealth axis no longer starts at zero.

## Still open

Each of these was weighed and left for a follow-up.

1. **Balances for past months.** They still go in one account at a time. A "Month End" sheet
   listing every account with its last balance prefilled would turn a recurring chore into
   one screen.
2. **Expenditure divisions.** The specification requires all fifteen. A "Hide empty
   divisions" option, on by default, is the right call if the specification allows it.
3. **Keyboard.** A shortcut for New Transaction (⌘N in the native window) and ←/→ for the
   month stepper.
4. **Currency labels.** Codes such as `CHF` would scan faster as symbols where they are
   unambiguous (`Fr.`, `€`, `$`).
5. **Transaction type labels** come from the domain ("Account transfer"). Shorter labels in
   the segmented control ("Transfer", "Exchange") would fit a phone without truncation.
6. **AG Grid's pagination bar** still looks like AG Grid. Fixed-height virtual scrolling would
   let it go.
