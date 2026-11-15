"""Build web logo assets from images/logo.png (black line art on transparency).

Run: uv run python scripts/make_logo_assets.py

The site draws the logo as a CSS mask filled with the theme's text colour, so only the alpha
channel matters. The hairline strokes of the original vanish at small sizes, so the small
versions are thickened before downscaling.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "images" / "logo.png"
OUT = ROOT / "app" / "static" / "img"

NAVY = (10, 17, 40, 255)
LIGHT = (233, 237, 249, 255)


def load_alpha():
    alpha = Image.open(SRC).convert("RGBA").getchannel("A")
    return alpha.crop(alpha.getbbox())


def bolden(alpha, work_height, grow, out_height):
    """Scale to work_height, thicken strokes (MaxFilter size `grow`), scale to out_height."""
    w = round(alpha.width * work_height / alpha.height)
    a = alpha.resize((w, work_height), Image.LANCZOS)
    if grow > 1:
        a = a.filter(ImageFilter.MaxFilter(grow))
    ow = round(a.width * out_height / a.height)
    return a.resize((ow, out_height), Image.LANCZOS)


def as_mask_png(alpha):
    img = Image.new("RGBA", alpha.size, (0, 0, 0, 0))
    img.putalpha(alpha)
    return img


def icon(alpha, size, pad_ratio=0.12):
    """Rounded navy square with the light logo, for favicons and the touch icon."""
    scale = 4  # draw big, then downsample for smooth edges
    s = size * scale
    canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(canvas).rounded_rectangle((0, 0, s - 1, s - 1), radius=s // 5, fill=NAVY)
    inner = int(s * (1 - 2 * pad_ratio))
    logo = alpha.copy()
    logo.thumbnail((inner, inner), Image.LANCZOS)
    glyph = Image.new("RGBA", logo.size, LIGHT)
    glyph.putalpha(logo)
    canvas.alpha_composite(glyph, ((s - logo.width) // 2, (s - logo.height) // 2))
    return canvas.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    base = load_alpha()
    # Header/footer mark: displayed ~56px tall, so ship 2x (retina) and thicken the strokes.
    mark = bolden(base, work_height=768, grow=9, out_height=128)
    as_mask_png(mark).save(OUT / "logo-mark.png", optimize=True)
    # Icons need even heavier strokes to survive at 32px.
    icon_alpha = bolden(base, work_height=768, grow=19, out_height=384)
    icon(icon_alpha, 180).save(OUT / "apple-touch-icon.png", optimize=True)
    icon(icon_alpha, 192).save(OUT / "icon-192.png", optimize=True)
    icon(icon_alpha, 32, pad_ratio=0.06).save(OUT / "favicon-32.png", optimize=True)
    for p in sorted(OUT.glob("*.png")):
        print(f"{p.name}: {Image.open(p).size} {p.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
