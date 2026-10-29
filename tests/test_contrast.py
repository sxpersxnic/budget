"""Text keeps WCAG AA contrast (4.5:1) on every surface in both appearances (F32); the tokens make it checkable."""

import re

import pytest

from budget_app.ui import theme

TEXT = ["label", "label-2", "label-3", "accent", "positive", "negative", "warning"]
SURFACES = ["bg", "surface", "surface-raised"]
FILLS = ["accent-strong", "negative-strong"]  # filled buttons with white text
AA = 4.5


def tokens(block: str) -> dict[str, str]:
    """The `--name: #rrggbb` tokens of one CSS rule of theme.CSS."""
    body = re.search(re.escape(block) + r"\s*\{(.*?)\}", theme.CSS, re.S)
    assert body, block
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", body.group(1)))


def luminance(hex_colour: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


LIGHT = tokens(":root")
DARK = {**LIGHT, **tokens("body.body--dark")}


@pytest.mark.parametrize("appearance", ["light", "dark"])
@pytest.mark.parametrize("text", TEXT)
@pytest.mark.parametrize("surface", SURFACES)
def test_text_on_surfaces(appearance: str, text: str, surface: str) -> None:
    colours = LIGHT if appearance == "light" else DARK
    assert contrast(colours[text], colours[surface]) >= AA, f"{text} on {surface}"


@pytest.mark.parametrize("appearance", ["light", "dark"])
@pytest.mark.parametrize("fill", FILLS)
def test_white_on_filled_buttons(appearance: str, fill: str) -> None:
    colours = LIGHT if appearance == "light" else DARK
    assert contrast("#ffffff", colours[fill]) >= AA


def test_quasar_brand_colours_follow_the_tokens() -> None:
    """ui.colors() sets the light values from Python; they must be the CSS tokens."""
    assert theme.ACCENT == LIGHT["accent"] == LIGHT["q-primary"]
    assert LIGHT["q-positive"] == LIGHT["positive"] and DARK["q-negative"] == DARK["negative"]


def test_contrast_is_computed_as_wcag_defines_it() -> None:
    assert contrast("#000000", "#ffffff") == pytest.approx(21)
    assert contrast("#777777", "#ffffff") == pytest.approx(4.48, abs=0.01)


def test_the_first_paint_uses_the_page_tokens() -> None:
    """Every page's HTML carries the background and dark tokens, so a page change never flashes white."""
    assert LIGHT["bg"] == theme.BG_LIGHT and DARK["bg"] == theme.BG_DARK
    assert f"--bg: {DARK['bg']};" in theme.DARK_TOKENS and "--surface:" in theme.DARK_TOKENS
    assert "prefers-color-scheme: dark" in theme.FIRST_PAINT and "@view-transition" in theme.FIRST_PAINT


def test_install_writes_the_first_paint_into_every_page(monkeypatch) -> None:
    heads: list[tuple[str, bool]] = []
    monkeypatch.setattr(theme.ui, "add_head_html", lambda code, shared=False: heads.append((code, shared)))
    monkeypatch.setattr(theme.ui, "add_css", lambda code, shared=False: None)
    theme.install()
    assert heads == [(theme.FIRST_PAINT, True)]
