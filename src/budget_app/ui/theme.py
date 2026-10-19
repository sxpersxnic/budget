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

CSS = r"""
:root {
  --font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro", "Segoe UI Variable", "Segoe UI",
          system-ui, "Helvetica Neue", sans-serif;
  --bg: #f5f5f7;
  --surface: #ffffff;
  --surface-raised: #ffffff;
  --sidebar: rgba(236, 236, 240, 0.72);
  --label: #1d1d1f;
  --label-2: #6e6e73;
  --label-3: #a1a1a6;
  --separator: rgba(60, 60, 67, 0.12);
  --separator-strong: rgba(60, 60, 67, 0.22);
  --fill: rgba(120, 120, 128, 0.12);
  --fill-2: rgba(120, 120, 128, 0.07);
  --accent: #0071e3;
  --accent-fill: rgba(0, 113, 227, 0.12);
  --positive: #248a3d;
  --negative: #d70015;
  --warning: #b25000;
  --warning-fill: rgba(255, 149, 0, 0.14);
  --shadow: 0 1px 2px rgba(0, 0, 0, 0.04), 0 2px 8px rgba(0, 0, 0, 0.04);
  --q-primary: #0071e3;
  --q-negative: #d70015;
  --q-positive: #248a3d;
}
body.body--dark {
  --bg: #000000;
  --surface: #1c1c1e;
  --surface-raised: #2c2c2e;
  --sidebar: rgba(28, 28, 30, 0.78);
  --label: #f5f5f7;
  --label-2: #a1a1a6;
  --label-3: #6e6e73;
  --separator: rgba(84, 84, 88, 0.45);
  --separator-strong: rgba(84, 84, 88, 0.75);
  --fill: rgba(120, 120, 128, 0.24);
  --fill-2: rgba(120, 120, 128, 0.14);
  --accent: #0a84ff;
  --accent-fill: rgba(10, 132, 255, 0.2);
  --positive: #30d158;
  --negative: #ff453a;
  --warning: #ff9f0a;
  --warning-fill: rgba(255, 159, 10, 0.16);
  --shadow: none;
  --q-primary: #0a84ff;
  --q-negative: #ff453a;
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

/* ---------------------------------------------------------------- sidebar */
.sidebar.q-drawer { background: var(--sidebar) !important; backdrop-filter: saturate(180%) blur(20px);
  border-right: 0.5px solid var(--separator) !important; }
body.body--dark .sidebar.q-drawer { background: #161618 !important; }
.sidebar .brand { font-size: 13px; font-weight: 600; color: var(--label-2); letter-spacing: 0; }
.nav-item { display: flex; align-items: center; gap: 10px; height: 32px; padding: 0 10px; border-radius: 7px;
  color: var(--label); font-size: 14px; cursor: pointer; text-decoration: none; user-select: none; }
.nav-item:hover { background: var(--fill-2); }
.nav-item.active { background: var(--fill); font-weight: 600; }
.nav-item .q-icon { color: var(--accent); font-size: 19px; }
.nav-footer { font-size: 11px; color: var(--label-3); line-height: 1.4; }
.mobile-bar { background: color-mix(in srgb, var(--bg) 82%, transparent) !important; color: var(--label) !important;
  backdrop-filter: saturate(180%) blur(20px); border-bottom: 0.5px solid var(--separator); }

/* ---------------------------------------------------------------- page chrome */
.page { width: 100%; max-width: 72rem; margin: 0 auto; padding: 28px 32px 64px; gap: 20px; }
.page.narrow { max-width: 40rem; }
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
@media (max-width: 700px) {
  .ledger-row { grid-template-columns: minmax(0, 1fr) auto auto 1.5rem; column-gap: 10px; padding: 0 2px; }
  .ledger-row .c-bar, .ledger-row .c-diff { display: none; }
}
.meter { height: 6px; border-radius: 3px; background: var(--fill); overflow: hidden; width: 100%; }
.meter > div { height: 100%; border-radius: 3px; background: var(--accent); }
.meter.over > div { background: var(--negative); }
.meter.lg { height: 8px; border-radius: 4px; }

/* ---------------------------------------------------------------- controls */
.q-btn { text-transform: none !important; letter-spacing: 0 !important; font-weight: 500;
  border-radius: 8px !important; }
.q-btn:before { box-shadow: none !important; }
.q-btn.bg-primary { box-shadow: none !important; }
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

.q-menu { border-radius: 10px !important; box-shadow: 0 8px 30px rgba(0,0,0,.16) !important;
  background: var(--surface-raised); }
.q-item { min-height: 34px; font-size: 14px; }
.q-notification { border-radius: 12px !important; }
.q-separator { background: var(--separator) !important; }
.q-toggle__inner--truthy .q-toggle__track { opacity: 1 !important; }
.q-toggle__inner--truthy .q-toggle__thumb:after { background: #fff !important; }

/* product list rows in the transaction form */
.item-row { display: grid; grid-template-columns: minmax(0, 1fr) 4.5rem 6.5rem 5.5rem 1.75rem; gap: 6px;
  align-items: center; width: 100%; }
.item-row.head { font-size: 12px; color: var(--label-2); font-weight: 500; padding: 0 2px; }
.item-row .r { text-align: right; }
@media (max-width: 520px) {
  .item-row { grid-template-columns: minmax(0, 1fr) 3.75rem 5.5rem 1.75rem; }
  .item-row .c-total { display: none; }
}

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
/* ---------------------------------------------------------------- utilities (last, so they win) */
.caption:empty { display: none; }
.text-pos { color: var(--positive) !important; }
.text-neg { color: var(--negative) !important; }
input[type=number]::-webkit-inner-spin-button, input[type=number]::-webkit-outer-spin-button {
  -webkit-appearance: none; margin: 0; }
input[type=number] { -moz-appearance: textfield; }

.selection-bar { display: flex; align-items: center; gap: 8px; padding: 6px 8px 6px 14px; border-radius: 10px;
  background: var(--accent-fill); width: 100%; font-size: 14px; }
"""


ACCENT = "#0071e3"


def install() -> None:
    """Register the stylesheet for every page (call once at startup)."""
    ui.add_css(CSS, shared=True)


def apply_colors() -> None:
    """Quasar brand colours for the current page; dark-mode variants come from the CSS above."""
    ui.colors(primary=ACCENT, secondary="#6e6e73", accent=ACCENT, positive="#248a3d", negative="#d70015")


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
            tickformat=",.0f",
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
