"""
fetch_boundaries.py — Download Peninsula city boundaries using the Census Bureau's
cartographic boundary shapefile (no REST API query syntax, no new dependencies).

Source: https://www2.census.gov/geo/tiger/GENZ2022/shp/cb_2022_06_place_500k.zip
  cb_2022_06_place_500k — California incorporated places, 1:500k resolution
  Coordinate system: WGS84 (EPSG:4326) — GeoJSON-ready, no reprojection needed.

Output: docs/data/boundaries.geojson
"""

import io
import json
import logging
import struct
import sys
import zipfile
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.config import DOCS_DATA, HEADERS, PENINSULA_JURISDICTIONS, REQUEST_TIMEOUT

log = logging.getLogger(__name__)

BOUNDARIES_PATH = DOCS_DATA / "boundaries.geojson"
SHP_CACHE       = DOCS_DATA / "cb_2022_06_place_500k.zip"

# Stable Census Bureau FTP URL — California places, 2022 vintage, 500k resolution
CENSUS_SHP_URL = (
    "https://www2.census.gov/geo/tiger/GENZ2022/shp/cb_2022_06_place_500k.zip"
)

INCORPORATED = [j for j in PENINSULA_JURISDICTIONS if j != "San Mateo County"]

# Census NAME field → canonical name used in viz_data.json
# Census TIGER names match city names exactly for our cities.
CITY_NAMES = set(INCORPORATED)


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_boundaries(force: bool = False) -> Path:
    if BOUNDARIES_PATH.exists() and not force:
        log.info("Using cached boundaries.geojson")
        _log_contents()
        return BOUNDARIES_PATH

    zip_bytes = _download_shapefile(force)
    if zip_bytes is None:
        log.error("Could not download Census shapefile — map will not render")
        BOUNDARIES_PATH.write_text('{"type":"FeatureCollection","features":[]}')
        return BOUNDARIES_PATH

    log.info("Parsing shapefile ...")
    features = _extract_features(zip_bytes)

    if not features:
        log.error(
            "Parsed 0 matching city features from shapefile.\n"
            "  Check that CENSUS_SHP_URL is still valid: %s", CENSUS_SHP_URL
        )
    else:
        log.info(
            "Extracted %d city features: %s",
            len(features),
            [f["properties"]["name"] for f in features],
        )
        missing = CITY_NAMES - {f["properties"]["name"] for f in features}
        if missing:
            log.warning("No boundary found for: %s", sorted(missing))

    geojson = {"type": "FeatureCollection", "features": features}
    BOUNDARIES_PATH.write_text(json.dumps(geojson, separators=(",", ":")))
    log.info("Saved boundaries.geojson (%.1f KB)", BOUNDARIES_PATH.stat().st_size / 1024)
    return BOUNDARIES_PATH


# ── Download ──────────────────────────────────────────────────────────────────

def _download_shapefile(force: bool) -> Optional[bytes]:
    if SHP_CACHE.exists() and not force:
        log.info("Using cached shapefile ZIP (%s)", SHP_CACHE.name)
        return SHP_CACHE.read_bytes()

    log.info("Downloading Census shapefile from %s ...", CENSUS_SHP_URL)
    try:
        r = requests.get(CENSUS_SHP_URL, headers=HEADERS, timeout=120, stream=True)
        r.raise_for_status()
        data = r.content
        SHP_CACHE.write_bytes(data)
        log.info("Downloaded %.1f KB", len(data) / 1024)
        return data
    except requests.RequestException as e:
        log.error("Download failed: %s", e)
        return None


# ── Shapefile parsing (pure stdlib, no external deps) ─────────────────────────

def _extract_features(zip_bytes: bytes) -> list:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        shp_name = next((n for n in names if n.endswith(".shp")), None)
        dbf_name = next((n for n in names if n.endswith(".dbf")), None)
        if not shp_name or not dbf_name:
            log.error("ZIP does not contain .shp/.dbf: %s", names)
            return []
        log.info("Reading %s and %s from ZIP", shp_name, dbf_name)
        shp_buf = zf.read(shp_name)
        dbf_buf = zf.read(dbf_name)

    records  = _parse_dbf(dbf_buf)
    geometries = _parse_shp(shp_buf)

    if len(records) != len(geometries):
        log.warning("Record count mismatch: %d dbf vs %d shp", len(records), len(geometries))

    features = []
    for rec, geom in zip(records, geometries):
        name = rec.get("NAME", "").strip()
        if name not in CITY_NAMES:
            continue
        if geom is None:
            log.warning("No geometry for %s", name)
            continue
        features.append({
            "type": "Feature",
            "properties": {"name": name, "geoid": rec.get("GEOID", "")},
            "geometry": geom,
        })
    return features


def _parse_dbf(buf: bytes) -> list[dict]:
    """Parse dBASE III DBF file → list of dicts (standard library only)."""
    if len(buf) < 32:
        return []
    n_records  = struct.unpack_from("<I", buf, 4)[0]
    header_sz  = struct.unpack_from("<H", buf, 8)[0]
    record_sz  = struct.unpack_from("<H", buf, 10)[0]

    # Field descriptors: 32 bytes each, terminated by 0x0D
    fields = []
    pos = 32
    while pos < header_sz - 1 and buf[pos] != 0x0D:
        raw_name = buf[pos : pos + 11].split(b"\x00")[0]
        fname    = raw_name.decode("latin-1").strip()
        flen     = buf[pos + 16]
        fields.append((fname, flen))
        pos += 32

    records = []
    pos = header_sz
    for _ in range(n_records):
        if pos >= len(buf):
            break
        deleted = buf[pos]
        pos += 1
        row = {}
        for fname, flen in fields:
            chunk = buf[pos : pos + flen]
            row[fname] = chunk.decode("latin-1", errors="replace").strip()
            pos += flen
        if deleted != 0x2A:   # 0x2A = '*' means deleted
            records.append(row)
    return records


def _parse_shp(buf: bytes) -> list:
    """Parse ESRI Shapefile .shp → list of GeoJSON geometry dicts (type 5 = Polygon)."""
    geometries = []
    pos = 100   # skip 100-byte file header
    while pos + 8 <= len(buf):
        # Record header: record number (big-endian), content length in 16-bit words (big-endian)
        content_len = struct.unpack_from(">I", buf, pos + 4)[0] * 2
        pos += 8
        if pos + content_len > len(buf):
            break

        shape_type = struct.unpack_from("<I", buf, pos)[0]

        if shape_type == 0:   # Null shape
            geometries.append(None)
            pos += content_len
            continue

        if shape_type not in (5, 15, 25):   # Polygon variants
            geometries.append(None)
            pos += content_len
            continue

        # Polygon record layout (after shape type):
        #   bbox: 4×double (32 bytes) at offset 4
        #   num_parts: int32 at offset 36
        #   num_points: int32 at offset 40
        #   parts[]: num_parts × int32 at offset 44
        #   points[]: num_points × (double, double) at offset 44 + num_parts×4
        num_parts  = struct.unpack_from("<I", buf, pos + 36)[0]
        num_points = struct.unpack_from("<I", buf, pos + 40)[0]
        parts_off  = pos + 44
        pts_off    = parts_off + num_parts * 4

        parts = [
            struct.unpack_from("<I", buf, parts_off + i * 4)[0]
            for i in range(num_parts)
        ]
        points = [
            [
                round(struct.unpack_from("<d", buf, pts_off + i * 16)[0], 5),
                round(struct.unpack_from("<d", buf, pts_off + i * 16 + 8)[0], 5),
            ]
            for i in range(num_points)
        ]

        rings = []
        for i, start in enumerate(parts):
            end = parts[i + 1] if i + 1 < num_parts else num_points
            rings.append(points[start:end])

        # Single ring = Polygon; multiple rings = still Polygon (outer + holes)
        # MultiPolygon handling: in Census shapefiles, separate polygons are
        # separate records, so we use Polygon for all.
        geometries.append({"type": "Polygon", "coordinates": rings})
        pos += content_len

    return geometries


def _log_contents():
    try:
        data = json.loads(BOUNDARIES_PATH.read_text())
        names = [f["properties"].get("name") for f in data.get("features", [])]
        log.info("boundaries.geojson has %d features: %s", len(names), names)
    except Exception:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    fetch_boundaries(force="--force" in sys.argv)
