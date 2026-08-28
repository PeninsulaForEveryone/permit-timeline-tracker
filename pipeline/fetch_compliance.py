"""
fetch_compliance.py — Download HCD's Housing Element Compliance Report.

This is a different dataset from the APR. It records, per jurisdiction, whether
HCD has found the adopted housing element in substantial compliance with state
housing element law, and when HCD last completed that review.

Why it matters here: the Builder's Remedy (Gov. Code 65589.5(d)(5)) and SB 423
streamlining are triggered by *compliance status*, not by permit production. No
amount of APR data can tell you whether a city is exposed to either — only this
report can, so the app reads it directly rather than inferring anything.

Compliance Status values (per HCD's published data dictionary):
    In           — adopted element is in compliance with state housing element law
    Conditional  — in compliance, predicated on certain conditions
    Out          — not in compliance, or adopted element not submitted on schedule
    Enforcement Out — HCD has moved the jurisdiction out of compliance through an
                      enforcement action (not in the dictionary; observed in the data)
"""

import logging
import sys
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.config import (
    COMPLIANCE_RAW,
    HCD_APR_CKAN_API,
    HCD_COMPLIANCE_CKAN_DATASET_ID,
    HCD_COMPLIANCE_URL,
    HEADERS,
    PENINSULA_JURISDICTIONS,
    REQUEST_TIMEOUT,
)

log = logging.getLogger(__name__)


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_compliance(force: bool = False) -> dict[str, dict]:
    """Return {jurisdiction: compliance record} for Peninsula jurisdictions.

    Returns {} rather than raising if the download fails — a missing compliance
    file should degrade the housing-element panel, not break the whole build.
    """
    path = _ensure_raw(force)
    if path is None:
        return {}

    df = pd.read_csv(path, dtype=str).rename(columns=lambda c: c.strip())
    df["_juris"] = df["Jurisdiction"].astype(str).str.strip().str.lower()

    lower_map = {j.lower(): j for j in PENINSULA_JURISDICTIONS}
    df = df[df["_juris"].isin(lower_map)].copy()
    if df.empty:
        log.warning("Compliance report contained no Peninsula jurisdictions")
        return {}

    df["_received"] = pd.to_datetime(df.get("Date Received"), errors="coerce")
    df["_reviewed"] = pd.to_datetime(df.get("Reviewed Date"), errors="coerce")

    records = {}
    for juris_lower, group in df.groupby("_juris"):
        # Latest record HCD holds for this jurisdiction is its current status
        row = group.sort_values("_received", na_position="first").iloc[-1]
        status = (row.get("Compliance Status") or "").strip()
        records[lower_map[juris_lower]] = {
            "compliance_status": status or None,
            "in_compliance": _in_compliance(status),
            "review_status": (row.get("Review Status") or "").strip() or None,
            "record_type": (row.get("Record Type") or "").strip() or None,
            "cycle": (row.get("CYCLE") or "").strip() or None,
            "date_received": _iso(row["_received"]),
            "reviewed_date": _iso(row["_reviewed"]),
        }

    missing = sorted(set(PENINSULA_JURISDICTIONS) - set(records))
    if missing:
        log.warning("No compliance record for: %s", ", ".join(missing))
    log.info("Compliance report: %d of %d jurisdictions matched",
             len(records), len(PENINSULA_JURISDICTIONS))
    return records


def _in_compliance(status: str) -> Optional[bool]:
    """True / False / None (unknown) from HCD's Compliance Status string."""
    s = (status or "").strip().lower()
    if not s:
        return None
    if "out" in s:            # "Out", "Enforcement Out"
        return False
    if s.startswith("in") or s.startswith("conditional"):
        return True
    return None


# ── Fetch / cache ─────────────────────────────────────────────────────────────

def _ensure_raw(force: bool) -> Optional[Path]:
    if COMPLIANCE_RAW.exists() and not force:
        log.info("Using cached %s", COMPLIANCE_RAW.name)
        return COMPLIANCE_RAW

    log.info("Downloading housing element compliance report ...")
    content = _download(HCD_COMPLIANCE_URL) or _download_via_ckan()
    if content is None:
        if COMPLIANCE_RAW.exists():
            log.warning("Download failed; falling back to stale cached %s", COMPLIANCE_RAW.name)
            return COMPLIANCE_RAW
        log.error("Could not download compliance report; housing element status unavailable")
        return None

    COMPLIANCE_RAW.write_bytes(content)
    log.info("Saved %s (%d KB)", COMPLIANCE_RAW.name, len(content) // 1024)
    return COMPLIANCE_RAW


def _download(url: str) -> Optional[bytes]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        return r.content
    except requests.RequestException as e:
        log.warning("Download failed (%s): %s", url, e)
        return None


def _download_via_ckan() -> Optional[bytes]:
    """Discover the current CSV URL via the CKAN API (resource IDs change)."""
    try:
        r = requests.get(
            HCD_APR_CKAN_API,
            params={"id": HCD_COMPLIANCE_CKAN_DATASET_ID},
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        for res in r.json().get("result", {}).get("resources", []):
            if (res.get("format") or "").upper() == "CSV" and res.get("url", "").endswith(".csv"):
                log.info("CKAN discovered URL: %s", res["url"])
                return _download(res["url"])
        log.warning("CKAN: no CSV resource found in compliance dataset")
    except Exception as e:
        log.warning("CKAN discovery failed: %s", e)
    return None


def _iso(ts) -> Optional[str]:
    return None if pd.isna(ts) else ts.date().isoformat()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    recs = fetch_compliance(force="--force" in sys.argv)
    for city in PENINSULA_JURISDICTIONS:
        r = recs.get(city)
        if not r:
            print(f"{city:<22} —")
            continue
        print(f"{city:<22} {r['compliance_status']:<16} reviewed={r['reviewed_date']} "
              f"({r['record_type']}, {r['review_status']})")
