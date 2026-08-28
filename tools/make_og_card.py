"""
make_og_card.py — Render docs/img.png, the social preview card.

Why this exists: the previous card was a ~square map (1144×1156). Every social
surface crops og:image to roughly 1.91:1, so iMessage/Slack/X were slicing the
top and bottom off the Peninsula and squeezing the labels. This renders the card
at exactly 1200×630 — the crop-free size — with the map laid out inside it.

Reads docs/data/boundaries.geojson and docs/data/viz_data.json, so the card
re-renders with current friction scores.

Usage:
    python tools/make_og_card.py            # writes docs/img.png
    python tools/make_og_card.py --out /tmp/preview.png

Requires Pillow (not needed by the data pipeline, so it is not in
requirements.txt):  pip install Pillow
"""

import argparse
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent.parent
GEOJSON = ROOT / "docs" / "data" / "boundaries.geojson"
VIZ_DATA = ROOT / "docs" / "data" / "viz_data.json"
DEFAULT_OUT = ROOT / "docs" / "img.png"

# Canonical Open Graph / Twitter summary_large_image size
W, H = 1200, 630
SS = 3  # supersampling factor for polygon antialiasing

# Palette lifted from docs/index.html
BG = (250, 249, 246)
INK = (28, 27, 24)
INK_2 = (74, 72, 68)
INK_3 = (122, 120, 116)
HIGH = (226, 75, 74)
MED = (186, 117, 23)
LOW = (99, 153, 34)
NODATA = (211, 209, 199)
STROKE = (250, 249, 246)

# macOS first, then the Linux paths the refresh workflow runs on
FONT_DIRS = [
    Path("/System/Library/Fonts/Supplemental"),
    Path("/Library/Fonts"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation"),
]
FONT_CANDIDATES = {
    "bold": ["Helvetica Bold.ttf", "Arial Bold.ttf",
             "LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    "regular": ["Helvetica.ttf", "Arial.ttf",
                "LiberationSans-Regular.ttf", "DejaVuSans.ttf"],
}


def load_font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    for name in FONT_CANDIDATES[weight]:
        for d in FONT_DIRS:
            if (d / name).exists():
                return ImageFont.truetype(str(d / name), size)
    return ImageFont.load_default(size)


def score_color(score):
    if score is None:
        return NODATA
    if score >= 65:
        return HIGH
    if score >= 35:
        return MED
    return LOW


# ── Geometry ─────────────────────────────────────────────────────────────────

def rings(feature):
    """Yield (ring, is_hole) coordinate rings for Polygon and MultiPolygon."""
    geom = feature["geometry"]
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    for poly in polys:
        for i, ring in enumerate(poly):
            yield ring, i > 0


def project(lon, lat, lat0):
    """Equirectangular, x scaled by cos(lat0) so the Peninsula isn't stretched."""
    return lon * math.cos(math.radians(lat0)), -lat


def draw_map(draw, features, scores, box):
    x0, y0, x1, y1 = box
    lats = [c[1] for f in features for r, _ in rings(f) for c in r]
    lons = [c[0] for f in features for r, _ in rings(f) for c in r]
    lat0 = sum(lats) / len(lats)

    pts = [project(lon, lat, lat0) for lon, lat in zip(lons, lats)]
    minx, maxx = min(p[0] for p in pts), max(p[0] for p in pts)
    miny, maxy = min(p[1] for p in pts), max(p[1] for p in pts)

    scale = min((x1 - x0) / (maxx - minx), (y1 - y0) / (maxy - miny))
    # Centre the projected shape inside the box
    ox = x0 + ((x1 - x0) - (maxx - minx) * scale) / 2
    oy = y0 + ((y1 - y0) - (maxy - miny) * scale) / 2

    def to_px(lon, lat):
        px, py = project(lon, lat, lat0)
        return (ox + (px - minx) * scale, oy + (py - miny) * scale)

    for f in features:
        name = f["properties"].get("name")
        fill = score_color(scores.get(name))
        for ring, is_hole in rings(f):
            xy = [to_px(lon, lat) for lon, lat in ring]
            if len(xy) < 3:
                continue
            draw.polygon(xy, fill=BG if is_hole else fill,
                         outline=STROKE, width=max(1, int(1.2 * SS)))


# ── Card ─────────────────────────────────────────────────────────────────────

def build(out_path: Path) -> Path:
    geo = json.loads(GEOJSON.read_text())
    data = json.loads(VIZ_DATA.read_text())
    cities = data["cities"]
    meta = data["metadata"]
    scores = {c["city"]: c["friction_score"] for c in cities}

    # Map first, on its own supersampled canvas, then downscaled onto the card
    map_box = (655, 28, 1155, 602)
    mw, mh = (map_box[2] - map_box[0]) * SS, (map_box[3] - map_box[1]) * SS
    layer = Image.new("RGB", (mw, mh), BG)
    draw_map(ImageDraw.Draw(layer), geo["features"], scores, (0, 0, mw, mh))
    layer = layer.resize((mw // SS, mh // SS), Image.LANCZOS)

    card = Image.new("RGB", (W, H), BG)
    card.paste(layer, (map_box[0], map_box[1]))
    d = ImageDraw.Draw(card)

    f_eyebrow = load_font("bold", 19)
    f_title = load_font("bold", 60)
    f_sub = load_font("regular", 27)
    f_stat = load_font("bold", 21)
    f_stat_lbl = load_font("regular", 19)
    f_legend = load_font("regular", 17)

    x = 72
    d.text((x, 92), "PENINSULA FOR EVERYONE", font=f_eyebrow, fill=INK_3)
    d.text((x, 132), "Peninsula Permit", font=f_title, fill=INK)
    d.text((x, 200), "Tracker", font=f_title, fill=INK)
    d.text((x, 292), "How long does your city take", font=f_sub, fill=INK_2)
    d.text((x, 328), "to approve housing?", font=f_sub, fill=INK_2)

    # Two facts, straight from the data the card is rendered from
    med = sorted(c["median_days_to_permit"] for c in cities
                 if c["median_days_to_permit"] is not None)
    typical = med[len(med) // 2] if med else None
    stats = [
        (f"{len(cities)} cities", "ranked on permit friction"),
        (f"{typical} days" if typical else "—", "median, submittal to building permit"),
    ]
    y = 396
    for value, label in stats:
        d.text((x, y), value, font=f_stat, fill=INK)
        d.text((x + d.textlength(value, font=f_stat) + 10, y + 2), label,
               font=f_stat_lbl, fill=INK_3)
        y += 34

    # Legend — the map has no city labels; this is what carries its meaning
    y = 500
    for color, label in [(HIGH, "High friction"), (MED, "Medium"), (LOW, "Low friction")]:
        d.rectangle([x, y + 3, x + 13, y + 16], fill=color)
        d.text((x + 22, y), label, font=f_legend, fill=INK_2)
        x += int(d.textlength(label, font=f_legend)) + 56

    yr = meta.get("latest_apr_year") or meta.get("latest_apr_year_table_a")
    d.text((72, 548), f"HCD Annual Progress Report data through {yr}",
           font=f_legend, fill=INK_3)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    card.save(out_path, "PNG", optimize=True)
    print(f"Wrote {out_path} ({card.size[0]}×{card.size[1]}, "
          f"{out_path.stat().st_size / 1024:.0f} KB)")
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Render the 1200×630 social card")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    sys.exit(0 if build(ap.parse_args().out) else 1)
