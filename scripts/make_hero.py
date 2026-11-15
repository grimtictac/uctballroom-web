"""Regenerate the homepage hero WebP variants from images/tent.jpg (already cropped by hand).

Run after replacing images/tent.jpg:  uv run python scripts/make_hero.py
The width is read from the source, never upscaled; app/static/css/site.css (--hero-ratio, the 1148 / 623 in
.hero-media) and app/templates/home.html must match the new size if the crop's proportions change.
"""
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app/static/img"


def main():
    im = Image.open(ROOT / "images/tent.jpg").convert("RGB")
    print("source", im.size)
    for w in (im.width // 2, im.width):
        h = round(im.height * w / im.width)
        dest = OUT / f"hero-{w}.webp"
        im.resize((w, h), Image.LANCZOS).save(dest, quality=80, method=6)
        print(dest.name, (w, h), dest.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
