"""
fetch_boundaries.py — Download Peninsula city boundaries from the Census Bureau
TIGERweb REST API and save as GeoJSON for the choropleth map.

Output: docs/data/boundaries.geojson
  FeatureCollection where each feature has:
    properties.name      — canonical city name matching viz_data.json
    properties.geoid     — Census GEOID (for dedup)
  geometry               — Polygon / MultiPolygon (EPSG:4326)

Notes:
  - Palo Alto is in Santa Clara County but included in our tracker.
  - "San Mateo County" (unincorporated) has no incorporated-place polygon;
    we fetch the county boundary from a separate layer and include it.
  - The TIGER API has no key requirement and is public domain.
"""

import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.config import DOCS_DATA, HEADERS, PENINSULA_JURISDICTIONS, REQUEST_TIMEOUT

log = logging.getLogger(__name__)

BOUNDARIES_PATH = DOCS_DATA / "boundaries.geojson"

# ── Census TIGERweb REST endpoints ────────────────────────────────────────────
# Incorporated Places layer (current vintage)
TIGER_PLACES_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/"
    "TIGERweb/Places_CouSub_ConCity_SubMCD/MapServer/4/query"
)
# Counties layer (for San Mateo County unincorporated boundary)
TIGER_COUNTIES_URL = (
    "https://tigerweb.geo.census.gov/arcgis/rest/services/"
    "TIGERweb/PUMA_TAD_TAZ_UGA_ZCTA/MapServer/11/query"
)

CA_FIPS = "06"
SMC_FIPS = "06081"  # San Mateo County

# Cities in our tracker that are incorporated places (excludes unincorporated county)
INCORPORATED = [j for j in PENINSULA_JURISDICTIONS if j != "San Mateo County"]

# Name normalization: Census uses some different capitalizations
CENSUS_NAME_MAP = {
    "ATHERTON": "Atherton",
    "BELMONT": "Belmont",
    "BRISBANE": "Brisbane",
    "BURLINGAME": "Burlingame",
    "COLMA": "Colma",
    "DALY CITY": "Daly City",
    "EAST PALO ALTO": "East Palo Alto",
    "FOSTER CITY": "Foster City",
    "HALF MOON BAY": "Half Moon Bay",
    "HILLSBOROUGH": "Hillsborough",
    "MENLO PARK": "Menlo Park",
    "MILLBRAE": "Millbrae",
    "PACIFICA": "Pacifica",
    "PALO ALTO": "Palo Alto",
    "PORTOLA VALLEY": "Portola Valley",
    "REDWOOD CITY": "Redwood City",
    "SAN BRUNO": "San Bruno",
    "SAN CARLOS": "San Carlos",
    "SAN MATEO": "San Mateo",
    "SOUTH SAN FRANCISCO": "South San Francisco",
    "WOODSIDE": "Woodside",
}


def fetch_boundaries(force: bool = False) -> Path:
    if BOUNDARIES_PATH.exists() and not force:
        log.info("Using cached boundaries.geojson")
        return BOUNDARIES_PATH

    log.info("Fetching city boundaries from Census TIGERweb ...")
    features = []

    # ── Incorporated places ───────────────────────────────────────────────────
    name_list = ",".join(f"'{n}'" for n in INCORPORATED)
    params = {
        "where": f"STATE='{CA_FIPS}' AND NAME IN ({name_list})",
        "outFields": "NAME,GEOID,AREALAND",
        "geometryPrecision": "5",
        "outSR": "4326",
        "f": "geojson",
        "resultRecordCount": "200",
    }
    data = _get(TIGER_PLACES_URL, params)
    if data:
        for feat in data.get("features", []):
            raw_name = feat.get("properties", {}).get("NAME", "").upper().strip()
            canonical = CENSUS_NAME_MAP.get(raw_name)
            if canonical:
                feat["properties"]["name"] = canonical
                feat["properties"]["geoid"] = feat["properties"].get("GEOID", "")
                features.append(feat)
                log.debug("Got boundary for %s", canonical)
            else:
                log.debug("Skipping Census place: %s", raw_name)
    else:
        log.warning("Places query returned no data")

    found = {f["properties"]["name"] for f in features}
    missing = set(INCORPORATED) - found
    if missing:
        log.warning("Missing boundaries for: %s", sorted(missing))

    # ── San Mateo County boundary (for unincorporated areas) ─────────────────
    county_params = {
        "where": f"GEOID='{SMC_FIPS}'",
        "outFields": "NAME,GEOID",
        "geometryPrecision": "5",
        "outSR": "4326",
        "f": "geojson",
    }
    county_data = _get(TIGER_COUNTIES_URL, county_params)
    if county_data and county_data.get("features"):
        feat = county_data["features"][0]
        feat["properties"]["name"] = "San Mateo County"
        feat["properties"]["geoid"] = SMC_FIPS
        features.append(feat)
        log.info("Got San Mateo County boundary")
    else:
        log.warning("Could not fetch San Mateo County boundary")

    geojson = {"type": "FeatureCollection", "features": features}
    BOUNDARIES_PATH.write_text(json.dumps(geojson, separators=(",", ":")))
    log.info(
        "Saved boundaries.geojson (%d features, %.1f KB)",
        len(features),
        BOUNDARIES_PATH.stat().st_size / 1024,
    )
    return BOUNDARIES_PATH


def _get(url: str, params: dict) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        log.error("Request failed (%s): %s", url, e)
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    fetch_boundaries(force="--force" in sys.argv)
