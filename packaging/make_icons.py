"""Generate the app icon (PNG, macOS .icns, Windows .ico) into packaging/assets/.

Run with: uv run --group build python packaging/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent / "assets"
SIZE = 1024
SCALE = 4  # draw large and downsample for smooth edges


def draw_icon() -> Image.Image:
    s = SIZE * SCALE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    # background: rounded square with a vertical blue gradient (macOS icon grid: ~10% margin)
    margin, radius = int(s * 0.09), int(s * 0.2)
    grad = Image.new("RGBA", (s, s))
    top, bottom = (79, 134, 184), (36, 72, 110)
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / (s - 1)
        gd.line([(0, y), (s, y)], fill=(*(int(a + (b - a) * t) for a, b in zip(top, bottom, strict=True)), 255))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([margin, margin, s - margin, s - margin], radius, fill=255)
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    # rising bars
    base_y = int(s * 0.74)
    bar_w, gap = int(s * 0.12), int(s * 0.05)
    x0 = int(s * 0.24)
    heights = (0.16, 0.25, 0.34)
    for i, h in enumerate(heights):
        x = x0 + i * (bar_w + gap)
        d.rounded_rectangle([x, base_y - int(s * h), x + bar_w, base_y], int(bar_w * 0.25), fill=(255, 255, 255, 245))
    # coin
    # coin resting above the tallest bar
    r = int(s * 0.085)
    cx = x0 + 2 * (bar_w + gap) + bar_w // 2
    cy = base_y - int(s * heights[-1]) - int(s * 0.035) - r
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(231, 160, 72, 255))
    ri = int(r * 0.72)
    d.ellipse([cx - ri, cy - ri, cx + ri, cy + ri], outline=(255, 236, 205, 255), width=int(s * 0.012))
    # baseline
    d.rounded_rectangle(
        [x0 - int(s * 0.03), base_y + int(s * 0.03), int(s * 0.79), base_y + int(s * 0.055)],
        int(s * 0.012),
        fill=(255, 255, 255, 200),
    )
    return img.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    icon = draw_icon()
    icon.save(ASSETS / "icon.png")
    icon.save(ASSETS / "icon.icns")
    icon.save(ASSETS / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icons written to", ASSETS)


if __name__ == "__main__":
    main()
