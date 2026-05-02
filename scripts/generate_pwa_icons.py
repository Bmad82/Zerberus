"""
Generator für PWA-Icons (Nala + Hel) im Kintsugi-Stil.

Erzeugt 4 Dateien unter zerberus/static/pwa/:
  - nala-192.png, nala-512.png  (Gold auf tiefem Blau)
  - hel-192.png,  hel-512.png   (Rot auf Anthrazit)

Aufruf:  python scripts/generate_pwa_icons.py

Das Skript ist deterministisch (kein RNG, fester Seed-loser Pfad), damit
Re-Runs Bytes-identische PNGs erzeugen — Git-Diff bleibt sauber, solange
die Konstanten unten unverändert sind.
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parents[1] / "zerberus" / "static" / "pwa"


# ---------------------------------------------------------------------------
# Theme-Definitionen (Farben aus den HTML-Köpfen von Nala/Hel)
# ---------------------------------------------------------------------------

NALA_THEME = {
    "bg":      (10, 22, 40, 255),     # #0a1628
    "accent":  (240, 180, 41, 255),   # #f0b429 (Gold)
    "accent2": (200, 148, 31, 255),   # #c8941f (Gold-Dark)
    "letter":  "N",
    "filename": "nala",
}

HEL_THEME = {
    "bg":      (26, 26, 26, 255),     # #1a1a1a
    "accent":  (255, 107, 107, 255),  # #ff6b6b
    "accent2": (200, 70, 70, 255),    # darker red
    "letter":  "H",
    "filename": "hel",
}


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Versuche systemweite Serif-Fonts, falle auf PIL-Default zurück."""
    candidates = [
        "georgia.ttf", "Georgia.ttf",
        "times.ttf",   "Times.ttf",
        "DejaVuSerif-Bold.ttf",
        "arial.ttf",   "Arial.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size=size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


def _draw_kintsugi_cracks(draw: ImageDraw.ImageDraw, size: int, color: tuple) -> None:
    """
    Zeichnet feine Gold-/Akzent-Linien diagonal über das Icon —
    Anlehnung an Kintsugi (japanische Bruchnaht-Reparatur mit Gold).
    Deterministisch: feste Linien, kein RNG.
    """
    line_w = max(1, size // 96)
    # Hauptdiagonale links-oben → rechts-unten (gebrochen in 3 Segmenten)
    pts1 = [
        (size * 0.10, size * 0.05),
        (size * 0.35, size * 0.30),
        (size * 0.55, size * 0.25),
        (size * 0.95, size * 0.55),
    ]
    for a, b in zip(pts1, pts1[1:]):
        draw.line([a, b], fill=color, width=line_w)

    # Zweite Naht rechts-oben → mitte-unten
    pts2 = [
        (size * 0.95, size * 0.10),
        (size * 0.78, size * 0.40),
        (size * 0.65, size * 0.65),
        (size * 0.45, size * 0.95),
    ]
    for a, b in zip(pts2, pts2[1:]):
        draw.line([a, b], fill=color, width=line_w)

    # Dritte feine Querlinie unten
    pts3 = [
        (size * 0.05, size * 0.78),
        (size * 0.30, size * 0.88),
        (size * 0.55, size * 0.82),
    ]
    for a, b in zip(pts3, pts3[1:]):
        draw.line([a, b], fill=color, width=max(1, line_w - 1))


def _render_icon(theme: dict, size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), theme["bg"])
    draw = ImageDraw.Draw(img)

    # 1) Sanfter Rand — leicht abgesetzte Border, damit das Icon auf dem
    #    Homescreen nicht mit dem dunklen Wallpaper verschwimmt.
    border_w = max(2, size // 64)
    draw.rectangle(
        [(0, 0), (size - 1, size - 1)],
        outline=theme["accent2"],
        width=border_w,
    )

    # 2) Kintsugi-Adern (vor dem Buchstaben → werden teilweise überdeckt)
    _draw_kintsugi_cracks(draw, size, theme["accent2"])

    # 3) Großer Initial-Buchstabe, zentriert
    font = _load_font(int(size * 0.62))
    letter = theme["letter"]
    bbox = draw.textbbox((0, 0), letter, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    # textbbox liefert manchmal negativen y0 (Ascent-Overhang) — kompensieren
    tx = (size - tw) / 2 - bbox[0]
    ty = (size - th) / 2 - bbox[1]
    draw.text((tx, ty), letter, fill=theme["accent"], font=font)

    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for theme in (NALA_THEME, HEL_THEME):
        for size in (192, 512):
            img = _render_icon(theme, size)
            out = OUT_DIR / f"{theme['filename']}-{size}.png"
            img.save(out, format="PNG", optimize=True)
            print(f"  wrote {out.relative_to(OUT_DIR.parents[1])} ({size}x{size})")


if __name__ == "__main__":
    main()
