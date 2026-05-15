"""
fetch_apr.py — Download and cache HCD APR Table A and Table A2 CSVs.

Handles:
  - Direct URL fetch with CKAN API fallback if URL has changed
  - In-place column name normalization via config.COLUMN_ALIASES
  - Filtering to Peninsula jurisdictions
  - Basic type coercion (dates, numerics)
  - Deduplication: keeps latest submission per (jurisdiction, year, address, unit_category)
"""

import io
import json
import logging
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.config import (
    COLUMN_ALIASES,
    DATA_RAW,
    HEADERS,
    HCD_APR_CKAN_API,
    HCD_APR_CKAN_DATASET_ID,
    HCD_TABLE_A2_URL,
    HCD_TABLE_A_URL,
    PENINSULA_JURISDICTIONS,
    REQUEST_TIMEOUT,
    TABLE_A2_RAW,
    TABLE_A_RAW,
    classify_unit_type,
)

log = logging.getLogger(__name__)

DATE_COLS = [
    "date_application_complete",
    "date_entitlement",
    "date_building_permit",
    "date_certificate_of_occupancy",
]

NUMERIC_COLS = [
    "reporting_year",
    "total_proposed_units",
    "total_approved_units",
    "very_low_income_units",
    "low_income_units",
    "moderate_income_units",
    "above_moderate_units",
]


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_all(force: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (table_a, table_a2) as normalized, Peninsula-filtered DataFrames."""
    table_a_path = _ensure_raw(TABLE_A_RAW, HCD_TABLE_A_URL, "tablea.csv", force)
    table_a2_path = _ensure_raw(TABLE_A2_RAW, HCD_TABLE_A2_URL, "tablea2.csv", force)

    df_a = _load_and_normalize(table_a_path, "Table A")
    df_a2 = _load_and_normalize(table_a2_path, "Table A2")

    df_a = _filter_peninsula(df_a)
    df_a2 = _filter_peninsula(df_a2)

    df_a = _coerce_types(df_a)
    df_a2 = _coerce_types(df_a2)

    df_a = _deduplicate(df_a, label="Table A")
    df_a2 = _deduplicate(df_a2, label="Table A2")

    log.info("Table A: %d rows across %d jurisdictions", len(df_a), df_a["jurisdiction"].nunique())
    log.info("Table A2: %d rows across %d jurisdictions", len(df_a2), df_a2["jurisdiction"].nunique())

    return df_a, df_a2


# ── Fetch / cache ─────────────────────────────────────────────────────────────

def _ensure_raw(cache_path: Path, primary_url: str, resource_name: str, force: bool) -> Path:
    if cache_path.exists() and not force:
        log.info("Using cached %s", cache_path.name)
        return cache_path

    log.info("Downloading %s ...", resource_name)
    content = _download(primary_url)
    if content is None:
        log.warning("Primary URL failed for %s; trying CKAN discovery ...", resource_name)
        content = _download_via_ckan(resource_name)
    if content is None:
        raise RuntimeError(
            f"Could not download {resource_name}. "
            "Check HCD_TABLE_A_URL / HCD_TABLE_A2_URL in config.py."
        )

    cache_path.write_bytes(content)
    log.info("Saved %s (%d KB)", cache_path.name, len(content) // 1024)
    return cache_path


def _download(url: str) -> Optional[bytes]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.content
    except requests.RequestException as e:
        log.warning("Download failed (%s): %s", url, e)
        return None


def _download_via_ckan(resource_name: str) -> Optional[bytes]:
    """Use the CKAN API to discover the current download URL for the dataset."""
    try:
        r = requests.get(
            HCD_APR_CKAN_API,
            params={"id": HCD_APR_CKAN_DATASET_ID},
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        pkg = r.json()
        resources = pkg.get("result", {}).get("resources", [])
        # Match by name fragment
        fragment = resource_name.lower().replace(".csv", "")
        for res in resources:
            name = res.get("name", "").lower()
            url = res.get("url", "")
            if fragment in name and url.endswith(".csv"):
                log.info("CKAN discovered URL: %s", url)
                return _download(url)
        log.warning("CKAN: no matching resource found for '%s'", resource_name)
    except Exception as e:
        log.warning("CKAN discovery failed: %s", e)
    return None


# ── Normalization ─────────────────────────────────────────────────────────────

def _load_and_normalize(path: Path, label: str) -> pd.DataFrame:
    log.info("Loading %s from %s ...", label, path.name)
    # Try UTF-8 first, fall back to latin-1 (some APR CSVs have encoding issues)
    for enc in ("utf-8", "latin-1"):
        try:
            df = pd.read_csv(path, encoding=enc, low_memory=False, dtype=str)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError(f"Could not decode {path}")

    df.columns = [c.strip() for c in df.columns]
    log.info("%s raw columns: %s", label, list(df.columns))
    df = _rename_columns(df)
    df = _add_unit_type(df)
    return df


def _rename_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map source column names → canonical names using COLUMN_ALIASES."""
    rename_map = {}
    existing = set(df.columns)
    for canonical, variants in COLUMN_ALIASES.items():
        if canonical in existing:
            continue  # already correct
        for v in variants:
            if v in existing:
                rename_map[v] = canonical
                break
    if rename_map:
        df = df.rename(columns=rename_map)
    # Ensure all canonical columns exist (fill missing with NaN)
    for canonical in COLUMN_ALIASES:
        if canonical not in df.columns:
            df[canonical] = pd.NA
    return df


def _add_unit_type(df: pd.DataFrame) -> pd.DataFrame:
    df["unit_type"] = df["unit_category"].apply(classify_unit_type)
    df["is_adu"] = df["unit_type"].isin(("ADU", "JADU"))
    return df


# ── Filtering ─────────────────────────────────────────────────────────────────

def _filter_peninsula(df: pd.DataFrame) -> pd.DataFrame:
    if "jurisdiction" not in df.columns:
        log.warning("No 'jurisdiction' column found; returning all rows")
        return df
    df["jurisdiction"] = df["jurisdiction"].str.strip()
    log.info("Sample jurisdiction values: %s", df["jurisdiction"].dropna().unique()[:8].tolist())
    # Case-insensitive match against our list
    lower_map = {j.lower(): j for j in PENINSULA_JURISDICTIONS}
    df["jurisdiction"] = df["jurisdiction"].apply(
        lambda x: lower_map.get(str(x).lower().strip(), None)
    )
    before = len(df)
    df = df[df["jurisdiction"].notna()].copy()
    log.info("Filtered %d → %d rows (Peninsula jurisdictions)", before, len(df))
    return df


# ── Type coercion ─────────────────────────────────────────────────────────────

def _coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    for col in DATE_COLS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "reporting_year" in df.columns:
        df["reporting_year"] = df["reporting_year"].astype("Int64")

    return df


# ── Deduplication ─────────────────────────────────────────────────────────────

def _deduplicate(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """
    HCD APR data can include duplicate entries when jurisdictions resubmit.
    Strategy: for each (jurisdiction, reporting_year, address, unit_type),
    keep the row with the most-complete date information.
    """
    key = ["jurisdiction", "reporting_year", "address", "unit_type"]
    key = [c for c in key if c in df.columns]
    if not key:
        return df

    date_count = df[DATE_COLS].notna().sum(axis=1)
    df = df.copy()
    df["_date_completeness"] = date_count
    df = df.sort_values("_date_completeness", ascending=False)
    before = len(df)
    df = df.drop_duplicates(subset=key, keep="first").drop(columns=["_date_completeness"])
    removed = before - len(df)
    if removed:
        log.info("%s: removed %d duplicate rows", label, removed)
    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    a, a2 = fetch_all(force="--force" in sys.argv)
    print(f"Table A shape: {a.shape}")
    print(f"Table A2 shape: {a2.shape}")
    print("\nTable A sample:")
    print(a[["jurisdiction", "reporting_year", "unit_type", "date_application_complete"]].head(10))
