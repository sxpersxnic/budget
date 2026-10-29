"""Design system: colour tokens (light and dark), typography and Quasar/AG Grid overrides.

Every colour the UI uses is a CSS custom property defined here, so dark mode is one block of overrides.
Quasar toggles `body.body--dark`, and AG Grid follows the same class, so the tokens key on it too.
"""

from __future__ import annotations

from nicegui import ui

# Plotly cannot read CSS variables; these mid-tone values stay legible on light and dark surfaces.
CHART_TEXT = "#8e8e93"
CHART_GRID = "rgba(142,142,147,0.18)"
CHART_FONT = "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', system-ui, sans-serif"
# Apple system colours (mid values that work in both appearances); income green and spending blue come first.
SERIES = ["#0a84ff", "#30b94d", "#ff9f0a", "#af52de", "#30b0c7", "#ff375f", "#5e5ce6", "#a2845e"]
POSITIVE = "#30b94d"
NEGATIVE = "#ff453a"
NEUTRAL = "#8e8e93"
# Tag colours (F10, the keys of models.TAG_COLOURS): (mid tone for charts and chip fills, text on light, text on dark)
TAG_SHADES = {
    "grey": ("#8e8e93", "#636366", "#c7c7cc"),
    "red": ("#ff453a", "#c4001a", "#ff8a80"),
    "orange": ("#ff9f0a", "#b04a00", "#ffb84d"),
    "yellow": ("#ffcc00", "#8a6100", "#ffd84d"),
    "green": ("#30b94d", "#1d7a33", "#5fe07a"),
    "teal": ("#30b0c7", "#00708a", "#70d7ff"),
    "blue": ("#0a84ff", "#0050c8", "#70b4ff"),
    "purple": ("#af52de", "#7f2fae", "#d9a0ff"),
    "pink": ("#ff375f", "#c2003b", "#ff8aa5"),
}

CSS = r"""
:root {
  --font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro", "Segoe UI Variable", "Segoe UI",
          system-ui, "Helvetica Neue", sans-serif;
  --bg: #f5f5f7;
  --surface: #ffffff;
  --surface-raised: #ffffff;
  --sidebar: rgba(236, 236, 240, 0.72);
  --label: #1d1d1f;
  --label-2: #5f5f64;
  --label-3: #707075;
  --separator: rgba(60, 60, 67, 0.12);
  --separator-strong: rgba(60, 60, 67, 0.22);
  --fill: rgba(120, 120, 128, 0.12);
  --fill-2: rgba(120, 120, 128, 0.07);
  --accent: #0066cc;
  --accent-strong: #0066cc;
  --accent-fill: rgba(0, 113, 227, 0.12);
  --positive: #1f7a35;
  --negative: #d70015;
  --negative-strong: #d70015;
  --warning: #b25000;
  --warning-fill: rgba(255, 149, 0, 0.14);
  --shadow: 0 1px 2px rgba(0, 0, 0, 0.04), 0 2px 8px rgba(0, 0, 0, 0.04);
  --q-primary: #0066cc;
  --q-negative: #d70015;
  --q-positive: #1f7a35;
}
body.body--dark {
  --bg: #000000;
  --surface: #1c1c1e;
  --surface-raised: #2c2c2e;
  --sidebar: rgba(28, 28, 30, 0.78);
  --label: #f5f5f7;
  --label-2: #aeaeb2;
  --label-3: #98989d;
  --separator: rgba(84, 84, 88, 0.45);
  --separator-strong: rgba(84, 84, 88, 0.75);
  --fill: rgba(120, 120, 128, 0.24);
  --fill-2: rgba(120, 120, 128, 0.14);
  --accent: #409cff;
  --accent-strong: #0068d1;
  --accent-fill: rgba(10, 132, 255, 0.2);
  --positive: #30d158;
  --negative: #ff6961;
  --negative-strong: #d70015;
  --warning: #ff9f0a;
  --warning-fill: rgba(255, 159, 10, 0.16);
  --shadow: none;
  --q-primary: #409cff;
  --q-negative: #ff6961;
  --q-positive: #30d158;
  --q-dark: #1c1c1e;
  --q-dark-page: #000000;
}

/* ---------------------------------------------------------------- base */
html, body, .q-layout, .q-field, .q-btn, .q-item, .q-menu, .q-tooltip, .q-notification, .q-dialog {
  font-family: var(--font) !important;
}
body { background: var(--bg) !important; color: var(--label); -webkit-font-smoothing: antialiased; }
.q-page-container, .q-layout { background: var(--bg); }
.num, .q-field__native[type=number] { font-variant-numeric: tabular-nums; }
.text-2 { color: var(--label-2); }
.text-3 { color: var(--label-3); }
.caption { font-size: 12px; color: var(--label-2); line-height: 1.35; }
.footnote { font-size: 13px; color: var(--label-2); line-height: 1.4; }

/* ---------------------------------------------------------------- keyboard (F32) */
:focus-visible, .q-btn:focus-visible, .nav-item:focus-visible { outline: 2px solid var(--accent) !important;
  outline-offset: 2px; }
.q-btn:focus-visible .q-focus-helper { display: none; }
.skip-link { position: fixed; left: 12px; top: -48px; z-index: 7000; padding: 8px 14px; border-radius: 8px;
  background: var(--surface-raised); color: var(--accent); font-weight: 600; text-decoration: none;
  box-shadow: var(--shadow); transition: top .15s; }
.skip-link:focus { top: 12px; }
.page:focus { outline: none; }
.shortcuts { display: grid; grid-template-columns: max-content 1fr; gap: 10px 16px; align-items: center;
  padding: 4px 0 12px; font-size: 14px; }
.shortcuts .keys { white-space: nowrap; }
kbd { display: inline-block; min-width: 22px; padding: 1px 6px; margin-right: 3px; border-radius: 5px;
  border: 0.5px solid var(--separator-strong); background: var(--fill-2); color: var(--label);
  font: 500 12px/1.5 var(--font); text-align: center; }

/* ---------------------------------------------------------------- sidebar */
.sidebar.q-drawer { background: var(--sidebar) !important; backdrop-filter: saturate(180%) blur(20px);
  border-right: 0.5px solid var(--separator) !important; }
body.body--dark .sidebar.q-drawer { background: #161618 !important; }
.sidebar .brand { font-size: 13px; font-weight: 600; color: var(--label-2); letter-spacing: 0; }
.nav-item { display: flex; align-self: stretch; align-items: center; gap: 10px; height: 32px; padding: 0 10px;
  border-radius: 7px;
  color: var(--label); font-size: 14px; cursor: pointer; text-decoration: none; user-select: none; }
.nav-item:hover { background: var(--fill-2); }
.nav-item.active { background: var(--fill); font-weight: 600; }
.nav-item .q-icon { color: var(--accent); font-size: 19px; }
.nav-badge { font-size: 11px; font-weight: 600; min-height: 17px; padding: 0 6px; }
.nav-footer { font-size: 11px; color: var(--label-3); line-height: 1.4; }
.footer-link.q-btn { padding: 0; min-height: 0; margin-top: 4px; font-weight: 400; color: var(--label-3); }
.footer-link.q-btn:hover { color: var(--accent); }
.mobile-bar { background: color-mix(in srgb, var(--bg) 82%, transparent) !important; color: var(--label) !important;
  backdrop-filter: saturate(180%) blur(20px); border-bottom: 0.5px solid var(--separator); }

/* ---------------------------------------------------------------- page chrome */
.page { width: 100%; max-width: 72rem; margin: 0 auto; padding: 28px 32px 64px; gap: 20px; }
/* every page has the same column, so the title never moves; a narrow page keeps its lists to a readable width,
   left-aligned under the title (UX review before 1.0) */
.page.narrow > * { max-width: 46rem; }
/* the pages of a place, as tabs under the title */
.section-tabs { display: flex; gap: 4px; flex-wrap: wrap; margin-top: -8px; border-bottom: 0.5px solid var(--separator);
  width: 100%; }
.section-tab { display: flex; align-items: center; gap: 6px; padding: 6px 12px 8px; margin-bottom: -0.5px;
  color: var(--label-2); font-size: 14px; text-decoration: none; border-bottom: 2px solid transparent; }
.section-tab:hover { color: var(--label); }
.section-tab.active { color: var(--label); font-weight: 600; border-bottom-color: var(--accent); }
.page.wide { max-width: 100rem; }  /* the budget page: room for two tables side by side */
@media (max-width: 640px) { .page { padding: 16px 16px 48px; gap: 16px; } }
.large-title { font-size: 28px; font-weight: 700; letter-spacing: -0.022em; line-height: 1.15; color: var(--label); }
.title-sub { font-size: 13px; color: var(--label-2); margin-top: 2px; }
.section-title { font-size: 17px; font-weight: 600; letter-spacing: -0.01em; color: var(--label); }
.group-header { font-size: 13px; font-weight: 600; color: var(--label-2); padding: 0 4px 6px; }

/* ---------------------------------------------------------------- surfaces */
.q-card, .surface { background: var(--surface) !important; color: var(--label); border-radius: 12px !important;
  box-shadow: var(--shadow) !important; }
body.body--dark .q-card, body.body--dark .surface { border: 0.5px solid var(--separator); }
.surface { padding: 16px 20px; width: 100%; }
@media (max-width: 640px) { .surface { padding: 14px 14px; } }
/* a card the user just added: a short accent ring so the eye finds it */
@keyframes just-added { from { box-shadow: 0 0 0 3px var(--q-primary), var(--shadow); } }
.just-added { animation: just-added 1.6s ease-out; scroll-margin-top: 16px; }
.sheet { padding: 20px 22px 18px !important; border-radius: 14px !important;
  background: var(--surface-raised) !important;
  gap: 14px; }
.sheet-title { font-size: 17px; font-weight: 600; letter-spacing: -0.01em; }
.q-dialog__backdrop { backdrop-filter: blur(2px); }

/* grouped list (System Settings style) */
.group { background: var(--surface); border-radius: 12px; width: 100%; overflow: hidden; box-shadow: var(--shadow); }
body.body--dark .group { border: 0.5px solid var(--separator); }
.group-row { display: flex; align-items: center; gap: 12px; min-height: 46px; padding: 6px 16px; position: relative;
  font-size: 14px; }
.group-row + .group-row::before { content: ""; position: absolute; top: 0; left: 16px; right: 0;
  border-top: 0.5px solid var(--separator); }
.group-row .row-actions { opacity: 0; transition: opacity .12s; }
.group-row:hover .row-actions, .group-row:focus-within .row-actions { opacity: 1; }
@media (hover: none) { .group-row .row-actions { opacity: 1; } }
.link-btn { color: var(--accent) !important; font-weight: 500; }

/* metric strip */
.metrics { display: grid; grid-template-columns: repeat(var(--n, 3), minmax(0, 1fr)); width: 100%; }
.metric { padding: 2px 20px; border-left: 0.5px solid var(--separator); min-width: 0; }
.metric:first-child { border-left: 0; padding-left: 0; }
.metric-label { font-size: 13px; color: var(--label-2); font-weight: 500; }
.metric-value { font-size: 24px; font-weight: 600; letter-spacing: -0.015em; font-variant-numeric: tabular-nums;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.metric-value.hero { font-size: 40px; font-weight: 700; letter-spacing: -0.03em; line-height: 1.1; }
.metric-value .cur { font-size: 0.55em; font-weight: 500; color: var(--label-2); margin-left: 4px; letter-spacing: 0;
  }
.metric-sub { font-size: 12px; color: var(--label-2); margin-top: 2px; }
@media (max-width: 760px) {
  .metrics { grid-template-columns: minmax(0, 1fr); row-gap: 14px; }
  .metric { border-left: 0; padding: 0; }
  .metric + .metric { border-top: 0.5px solid var(--separator); padding-top: 12px; }
  .metric-value.hero { font-size: 34px; }
}

/* net worth by asset class (Overview, F21) */
.class-bar { display: flex; gap: 2px; height: 10px; border-radius: 5px; overflow: hidden; width: 100%; }
.class-seg { min-width: 3px; }
.class-legend { display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); gap: 6px 24px;
  width: 100%; }
.class-dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
/* notable this month (Overview, F18): icon · title over its reason · amount · chevron, each row a link */
.nicegui-link.notable-row { display: grid; grid-template-columns: 22px minmax(0, 1fr) auto 18px; column-gap: 10px;
  align-items: center; padding: 7px 6px; margin: 0 -6px; border-radius: 8px; color: var(--label);
  text-decoration: none; }
.nicegui-link.notable-row:hover { background: var(--fill-2); opacity: 1; }
.notable-row .n-title { font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.notable-row .n-sub { font-size: 12px; color: var(--label-2); line-height: 1.35; }
.notable-row .n-amount { font-size: 14px; font-weight: 500; font-variant-numeric: tabular-nums; white-space: nowrap; }
.notable-warn { color: var(--warning); }

/* the lock page (F27) and the phone pages (F35): one card, no sidebar */
.quick-card { max-width: 26rem; }
.pairing-code { font: 600 34px/1.2 ui-monospace, Menlo, Consolas, monospace; letter-spacing: 0.12em; }
.pairing-qr { width: 10rem; height: 10rem; image-rendering: pixelated; }
.lock-page { width: 100%; min-height: 100vh; justify-content: center; padding: 24px 16px; }
.lock-card { width: 100%; max-width: 22rem; background: var(--surface); border-radius: 14px; padding: 24px 22px;
  box-shadow: var(--shadow); }
body.body--dark .lock-card { border: 0.5px solid var(--separator); }
.mono, .mono .q-field__native { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  letter-spacing: 0.04em; }
.recovery-key { font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: 17px; font-weight: 600;
  letter-spacing: 0.06em; text-align: center; padding: 10px 12px; border-radius: 10px; background: var(--fill-2);
  user-select: all; word-break: break-all; }

/* callout */
.callout { display: flex; align-items: center; gap: 10px; width: 100%; padding: 10px 14px; border-radius: 10px;
  background: var(--warning-fill); color: var(--label); font-size: 13px; }
.callout .q-icon { color: var(--warning); }

/* ---------------------------------------------------------------- ledger (budget tables) */
.ledger { width: 100%; }
.ledger-row { display: grid; grid-template-columns: minmax(0, 1fr) 5rem 7rem 7rem 7rem 1.75rem; column-gap: 12px;
  align-items: center; min-height: 34px; padding: 0 6px; border-radius: 6px; font-size: 14px; }
.ledger-row.head { min-height: 26px; font-size: 12px; color: var(--label-2); font-weight: 500;
  border-bottom: 0.5px solid var(--separator); border-radius: 0; margin-bottom: 2px; }
.ledger-row.click { cursor: pointer; }
.ledger-row:not(.head):hover { background: var(--fill-2); }
.ledger-row .r { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.ledger-row .cell-label { display: flex; align-items: center; gap: 4px; min-width: 0; }
.ledger-row .cell-label .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ledger-row .code { color: var(--label-3); font-variant-numeric: tabular-nums; margin-right: 6px; font-size: 13px; }
.ledger-row.dim .name { color: var(--label-2); }
.ledger-row .edit { opacity: 0; transition: opacity .12s; color: var(--label-2); }
.ledger-row:hover .edit, .ledger-row .edit:focus { opacity: 1; }
@media (hover: none) { .ledger-row .edit { opacity: 1; } }
.ledger-row .chev { color: var(--label-3); transition: transform .15s; }
.ledger-row .chev.open { transform: rotate(90deg); }
/* the tables follow their own width, not the window's (the sidebar comes and goes) */
.ledger { container-type: inline-size; }
@container (max-width: 560px) {
  .ledger-row { grid-template-columns: minmax(0, 1fr) auto auto 1.5rem; column-gap: 10px; padding: 0 2px; }
  .ledger-row .c-bar, .ledger-row .c-diff { display: none; }
}
@container (min-width: 561px) and (max-width: 800px) {  /* the bar goes first, so names stay readable */
  .ledger-row { grid-template-columns: minmax(0, 1fr) 6rem 6rem 6rem 1.75rem; }
  .ledger-row .c-bar { display: none; }
}
/* F18: compared with another month, the budget column shows that month and the difference the change in %;
   narrow tables keep the change instead of the other month's amount */
@container (max-width: 560px) {
  .ledger.compare .ledger-row { grid-template-columns: minmax(0, 1fr) auto 4.5rem 1.5rem; }  /* changes align */
  .ledger.compare .ledger-row .c-diff { display: block; }
  .ledger.compare .ledger-row .c-ref { display: none; }
}
.ledger-row.target { background: var(--accent-fill); }  /* the row a link from the Overview opened */
/* income and expenditure side by side once each table gets about 750 px, stacked below that (B08, decision 1);
   narrower halves would cut the category names short */
.ledger-pair-wrap { width: 100%; container-type: inline-size; }
.ledger-pair { display: grid; grid-template-columns: minmax(0, 1fr); gap: 20px; }
@media (max-width: 640px) { .ledger-pair { gap: 16px; } }
@container (min-width: 1500px) {
  .ledger-pair { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start; }
}

/* account balances (F41): name · opening · closing · change (account currency) · value in the view currency · action */
.accounts { container-type: inline-size; }
.acct-row { display: grid; grid-template-columns: minmax(0, 1fr) 7.5rem 7.5rem 7rem 8.5rem 7.5rem 5.5rem;
  column-gap: 10px; grid-template-areas: "name open close change unexpl view action"; align-content: center;
  row-gap: 0; }
.acct-row > * { white-space: nowrap; }
.acct-row .c-name { grid-area: name; min-width: 0; white-space: normal; }
.acct-row .c-open { grid-area: open; text-align: right; }
.acct-row .c-close { grid-area: close; text-align: right; }
.acct-row .c-change { grid-area: change; text-align: right; }
.acct-row .c-unexpl { grid-area: unexpl; text-align: right; justify-content: flex-end; }
.acct-row .c-view { grid-area: view; text-align: right; }
.acct-row .c-action { grid-area: action; justify-self: end; }
.acct-row.head { min-height: 30px; font-size: 12px; color: var(--label-2); font-weight: 500; }
@container (max-width: 760px) {  /* drop the opening balance and the view-currency value */
  .acct-row { grid-template-columns: minmax(0, 1fr) auto auto auto 2.25rem;
    grid-template-areas: "name close change unexpl action"; }
  .acct-row .c-open, .acct-row .c-view { display: none; }
  .acct-row .c-action .block { display: none; }  /* icon only */
}
@container (max-width: 460px) {  /* the change moves under the closing balance */
  .acct-row { grid-template-columns: minmax(0, 1fr) auto 2.25rem;
    grid-template-areas: "name close action" "name change action"; }
  .acct-row .c-change { font-size: 12px; }
  .acct-row .c-unexpl { display: none; }
}
/* COICOP tree (F37): code · name · subcategories · spent this year */
.cat-tree .q-tree__node-header { padding: 3px 6px; border-radius: 6px; margin-top: 0; }
.cat-tree .q-tree__node-header:hover { background: var(--fill-2); }
.cat-tree .q-tree__node-header-content { min-width: 0; }
.cat-node { display: flex; align-items: baseline; gap: 8px; width: 100%; min-width: 0; font-size: 14px;
  color: var(--label); }
.cat-node .code { color: var(--label-3); font-variant-numeric: tabular-nums; font-size: 13px; white-space: nowrap; }
.cat-node .name { min-width: 0; overflow-wrap: anywhere; }  /* an information page: names wrap, never cut */
.cat-node.lvl-1 .name { font-weight: 600; }
.cat-node .caption { white-space: nowrap; }
.cat-node .spent { margin-left: auto; padding-left: 12px; font-variant-numeric: tabular-nums; white-space: nowrap;
  color: var(--label-2); }
@media (max-width: 640px) { .cat-node .caption { display: none; } }
/* COICOP notes (F40): in the picker's tooltip, inside a picker option (touch) and below a tree row */
.cat-item { display: flex; flex-direction: column; width: 100%; min-width: 0; }
.cat-node .notes-btn { align-self: center; margin: -4px 0; color: var(--label-3); }
.cat-node .notes-btn[aria-expanded=true] { color: var(--accent); }
.cat-notes { font-size: 12.5px; line-height: 1.4; color: var(--label-2); white-space: normal; }
.cat-notes__part + .cat-notes__part { margin-top: 6px; }
.cat-notes__head { font-weight: 600; color: var(--label); font-size: 12px; }
.cat-notes ul { margin: 2px 0 0; padding-left: 16px; }
.cat-notes li { margin: 1px 0; }
.cat-notes__foot { margin-top: 6px; font-size: 11px; color: var(--label-3); font-style: italic; }
.cat-item > .cat-notes { padding: 4px 6px 8px 0; max-width: 72ch; }
.q-item .cat-notes { margin-top: 6px; }
.cat-notes-tip.q-tooltip { background: var(--surface-raised); color: var(--label); padding: 10px 12px;
  border-radius: 10px; box-shadow: 0 8px 30px rgba(0,0,0,.18), 0 0 0 0.5px var(--separator);
  max-height: 70vh; overflow: hidden; }
.cat-notes-tip .cat-notes { margin-top: 0; }
.reval-row { grid-template-columns: minmax(0, 1fr) 9rem; }
/* change history (F28): the details toggle reads like a caption, not a list item */
.history-details .q-item { min-height: 22px; padding: 2px 0; font-size: 12px; color: var(--label-2);
  background: transparent !important; }
.history-details .q-item__section--side { padding-left: 4px; }
.history-details .q-expansion-item__content { padding: 2px 0 4px; }
.meter { height: 6px; border-radius: 3px; background: var(--fill); overflow: hidden; width: 100%; }
.meter > div { height: 100%; border-radius: 3px; background: var(--accent); }
.meter.over > div { background: var(--negative); }
.meter.done > div { background: var(--positive); }
/* savings goals (F14): the amounts beside a goal's name, and the Overview card's goals side by side when wide */
.goal-amount { font-variant-numeric: tabular-nums; white-space: nowrap; }
.goal-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(16rem, 1fr)); gap: 14px 28px; width: 100%; }
.meter.lg { height: 8px; border-radius: 4px; }

/* ---------------------------------------------------------------- controls */
.q-btn { text-transform: none !important; letter-spacing: 0 !important; font-weight: 500;
  border-radius: 8px !important; }
.q-btn:before { box-shadow: none !important; }
.q-btn.bg-primary { box-shadow: none !important; }
/* filled buttons: white text keeps 4.5:1 in both appearances (F32) */
.bg-primary { background: var(--accent-strong) !important; }
.bg-negative { background: var(--negative-strong) !important; }
.q-btn--dense { border-radius: 7px !important; }
.q-btn--round { border-radius: 50% !important; }
.q-field--outlined .q-field__control { border-radius: 8px !important; background: var(--surface); }
body.body--dark .q-field--outlined .q-field__control { background: var(--fill-2); }
.q-field--outlined .q-field__control:before { border-color: var(--separator-strong) !important; }
.q-field--outlined.q-field--focused .q-field__control:after { border-width: 2px !important; }
.q-field__label { color: var(--label-2); }
.q-field--borderless .q-field__control { padding: 0 4px; }
.search .q-field__control { border-radius: 9px !important; background: var(--fill) !important; }
.search .q-field__control:before { border: 0 !important; }
.hero-amount input { font-size: 28px !important; font-weight: 600; letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums; }
.hero-amount .q-field__control { height: 64px !important; }

/* segmented control */
.segmented { background: var(--fill) !important; border-radius: 9px !important; padding: 2px;
  box-shadow: none !important;
  display: flex; }
.segmented .q-btn { color: var(--label) !important; background: transparent !important; border-radius: 7px !important;
  font-weight: 500; font-size: 13px; min-height: 28px; padding: 0 12px; flex: 1 1 auto; }
.segmented .q-btn[aria-pressed=true] { background: var(--surface-raised) !important; font-weight: 600;
  box-shadow: 0 1px 3px rgba(0,0,0,.12), 0 0 0 0.5px rgba(0,0,0,.04) !important; }
body.body--dark .segmented .q-btn[aria-pressed=true] { background: #636366 !important; }
.segmented.full { width: 100%; }
/* too narrow for all options (the transaction types on a phone): scroll inside, never spill over */
.segmented { overflow-x: auto; scrollbar-width: none; }
.segmented::-webkit-scrollbar { display: none; }
@media (max-width: 599px) { .segmented .q-btn { flex-shrink: 0; } }

.q-menu { border-radius: 10px !important; box-shadow: 0 8px 30px rgba(0,0,0,.16) !important;
  background: var(--surface-raised); }
.q-item { min-height: 34px; font-size: 14px; }
.q-notification { border-radius: 12px !important; }
.q-separator { background: var(--separator) !important; }
.q-toggle__inner--truthy .q-toggle__track { opacity: 1 !important; }
.q-toggle__inner--truthy .q-toggle__thumb:after { background: #fff !important; }

/* product list rows in the transaction form */
.item-row { display: grid; grid-template-columns: minmax(0, 1fr) 4.5rem 6.5rem 5.5rem 1.75rem 1.75rem; gap: 6px;
  align-items: center; width: 100%; }
.item-row.head { font-size: 12px; color: var(--label-2); font-weight: 500; padding: 0 2px; }
.item-row .r { text-align: right; }
/* F11: a line's own category and tags, below it and as wide as the row */
.item-split { grid-column: 1 / -1; display: grid; grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); gap: 6px;
  padding: 0 0 6px 12px; border-left: 2px solid var(--separator-strong); margin-left: 4px; }
@media (max-width: 520px) {
  .item-row { grid-template-columns: minmax(0, 1fr) 3.25rem 5rem 1.5rem 1.5rem; gap: 4px; }
  .item-row .c-total { display: none; }
  .item-split { grid-template-columns: minmax(0, 1fr); }
}

/* QR bills (F03) */
.bill-drop { padding: 22px 12px; font-size: 14px; flex-direction: column; text-align: center; }
.bill-drop .q-icon { font-size: 28px; }
.due-late { color: var(--negative); }
.due-soon { color: var(--warning); }
.bill-amount { font-variant-numeric: tabular-nums; white-space: nowrap; font-weight: 500; }
.closed-bills .nicegui-expansion-content { padding: 0; gap: 0; align-items: stretch; }

/* tax summary (F20) */
.tax-section + .tax-section { border-top: 0.5px solid var(--separator); }
.tax-section .q-item { padding: 6px 16px; }
.tax-section .nicegui-expansion-content { padding: 0 0 4px; gap: 0; align-items: stretch; }
.tax-section .group-row { padding-left: 28px; }
.tax-section .group-row + .group-row::before { left: 28px; }
.tax-tags { min-height: 36px; flex-wrap: wrap; gap: 6px; }
.tax-note { align-items: flex-start; }
.tax-links { max-height: 62vh; overflow-y: auto; padding-right: 4px; }

/* attachments in the transaction form (F12) */
.attach-drop { display: flex; align-items: center; justify-content: center; gap: 6px; width: 100%;
  padding: 10px 12px; border: 1px dashed var(--separator-strong); border-radius: 10px; cursor: pointer;
  color: var(--label-2); font-size: 13px; }
.attach-drop:hover { background: var(--fill-2); color: var(--accent); border-color: var(--accent); }
.attach-row { display: grid; grid-template-columns: 40px minmax(0, 1fr) auto; gap: 10px; align-items: center;
  width: 100%; padding: 4px 0; }
.attach-thumb { width: 40px; height: 40px; border-radius: 8px; overflow: hidden; background: var(--fill-2);
  display: flex; align-items: center; justify-content: center; color: var(--label-2); }
.attach-name { overflow: hidden; align-items: stretch; }
.attach-name .ellipsis { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-size: 14px;
  max-width: 100%; }
.nicegui-link.attach-link { display: inline-flex; align-items: center; justify-content: center; width: 32px;
  height: 32px; color: var(--accent); text-decoration: none; border-radius: 50%; }
.nicegui-link.attach-link:hover { background: var(--fill-2); }
.attach-preview { border-radius: 10px; overflow: hidden; background: var(--fill-2); }
.attach-preview-image { width: 100%; max-height: 360px; }
.attach-preview-pdf { width: 100%; height: 420px; border: 0; display: block; background: #fff; }
/* the receipt sheet (F04): the receipt beside the form, below it on a phone */
.receipt-side { width: 22rem; max-width: 100%; flex: 0 0 auto; position: sticky; top: 0; }
@media (max-width: 640px) { .receipt-side { width: 100%; position: static; } }
.receipt-image { width: 100%; max-height: 70vh; border-radius: 10px; background: var(--fill-2); }
@media (max-width: 640px) { .receipt-image { max-height: 40vh; } }
.clip-header .ag-header-cell-text { font-size: 0; }
.clip-header .ag-header-cell-text::before { content: "attach_file"; font-family: "Material Icons"; font-size: 17px; }
.clip-cell { display: inline-flex; align-items: center; gap: 2px; color: var(--label-2); }
.clip-cell .material-icons { font-size: 16px; }

/* indented, dependent group of fields */
.subform { border-left: 2px solid var(--separator); padding-left: 14px; margin-left: 4px; }

/* ---------------------------------------------------------------- AG Grid */
.nicegui-aggrid {
  --ag-font-family: var(--font);
  --ag-font-size: 13px;
  --ag-background-color: var(--surface);
  --ag-header-background-color: var(--surface);
  --ag-foreground-color: var(--label);
  --ag-header-text-color: var(--label-2);
  --ag-header-font-weight: 500;
  --ag-border-color: var(--separator);
  --ag-row-border: 0.5px solid var(--separator);
  --ag-wrapper-border: none;
  --ag-wrapper-border-radius: 12px;
  --ag-row-hover-color: var(--fill-2);
  --ag-selected-row-background-color: var(--accent-fill);
  --ag-accent-color: var(--accent);
  --ag-checkbox-checked-background-color: var(--accent);
  --ag-header-column-border: none;
  --ag-column-border: none;
  --ag-cell-horizontal-padding: 14px;
  --ag-row-height: 40px;
  --ag-header-height: 38px;
  --ag-spacing: 7px;
  --ag-odd-row-background-color: transparent;
  border-radius: 12px; box-shadow: var(--shadow);
}
body.body--dark .nicegui-aggrid { border: 0.5px solid var(--separator); }
.nicegui-aggrid .ag-cell { font-variant-numeric: tabular-nums; }
.nicegui-aggrid .amt-in { color: var(--positive); }
.nicegui-aggrid .muted { color: var(--label-2); }
/* a category the import suggested (F07): not the user's choice until they keep it */
.nicegui-aggrid .suggested { color: var(--label-2); font-style: italic; }
/* ---------------------------------------------------------------- utilities (last, so they win) */
.caption:empty { display: none; }
.text-pos { color: var(--positive) !important; }
.text-neg { color: var(--negative) !important; }
input[type=number]::-webkit-inner-spin-button, input[type=number]::-webkit-outer-spin-button {
  -webkit-appearance: none; margin: 0; }
input[type=number] { -moz-appearance: textfield; }

.selection-bar { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 6px 8px 6px 14px;
  border-radius: 10px; background: var(--accent-fill); width: 100%; font-size: 14px; }
.selection-bar .q-btn { white-space: nowrap; }  /* narrow windows: whole buttons wrap, not their words */
"""


ACCENT = "#0066cc"


def tag_css() -> str:
    """One class per tag colour (`tag-blue` …): the chip's fill and its text, for light and dark mode."""
    fill = "background: color-mix(in srgb, var(--tag) 18%, transparent)"
    rules = [
        ".tag-chip { display: inline-flex; align-items: center; height: 20px; padding: 0 8px; border-radius: 10px;"
        " font-size: 12px; font-weight: 500; line-height: 20px; white-space: nowrap; max-width: 14rem;"
        f" overflow: hidden; text-overflow: ellipsis; {fill}; color: var(--tag-text); }}",
        ".q-chip.tag-chip { height: 22px; margin: 2px 4px 2px 0; padding: 0 4px 0 8px; display: inline-flex;"
        f" {fill} !important; color: var(--tag-text) !important; }}",
        ".q-chip.tag-chip .q-chip__icon--remove { opacity: 0.7; color: inherit; }",
        ".tag-chip.cursor-pointer:hover { background: color-mix(in srgb, var(--tag) 30%, transparent); }",
        ".tag-chips { display: inline-flex; gap: 4px; align-items: center; flex-wrap: nowrap; overflow: hidden; }",
        ".tag-new { --tag: #8e8e93; --tag-text: var(--label-2); outline: 1px dashed var(--separator-strong); }",
        ".tag-dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; background: var(--tag); }",
        ".tag-swatch { width: 26px; height: 26px; min-width: 26px !important; min-height: 26px !important;"
        " padding: 0 !important; border-radius: 50%; background: var(--tag) !important; }",
        ".tag-swatch.selected { box-shadow: 0 0 0 2px var(--surface-raised), 0 0 0 4px var(--tag); }",
    ]
    for name, (mid, light, dark) in TAG_SHADES.items():
        rules.append(f".tag-{name} {{ --tag: {mid}; --tag-text: {light}; }}")
        rules.append(f"body.body--dark .tag-{name} {{ --tag-text: {dark}; }}")
    return "\n".join(rules)


def install() -> None:
    """Register the stylesheet for every page (call once at startup)."""
    ui.add_css(CSS + "\n" + tag_css(), shared=True)


def apply_colors() -> None:
    """Quasar brand colours for the current page; dark-mode variants come from the CSS above."""
    ui.colors(primary=ACCENT, secondary="#5f5f64", accent=ACCENT, positive="#1f7a35", negative="#d70015")


def chart_layout(fig, currency: str):
    """Quiet chart chrome: no titles (the card has one), hairline grid, Swiss thousands separator."""
    fig.update_layout(
        margin=dict(l=8, r=8, t=8, b=8),
        height=320,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
        separators=".'",
        font=dict(family=CHART_FONT, size=12, color=CHART_TEXT),
        legend=dict(orientation="h", x=0, xanchor="left", y=1.02, yanchor="bottom", font=dict(size=12), title=None),
        yaxis=dict(
            title=None,
            gridcolor=CHART_GRID,
            zeroline=False,
            separatethousands=True,
            ticksuffix=f" {currency}" if currency else "",
            tickformat=",~f",  # whole numbers stay whole; small ranges keep their decimals (0.5)
            automargin=True,
            side="right",
        ),
        xaxis=dict(gridcolor="rgba(0,0,0,0)", linecolor=CHART_GRID, automargin=True, showline=True),
        bargap=0.35,
        bargroupgap=0.12,
        barcornerradius=3,
        hoverlabel=dict(font=dict(family=CHART_FONT)),
    )
    return fig


def figure_dict(fig) -> dict:
    """Plotly figure as a dict with the toolbar ('modebar') switched off; downloads live in the card menu."""
    data = fig.to_plotly_json()
    data["config"] = {"displayModeBar": False, "responsive": True}
    return data
