"""
transform.py — Compute permit pipeline metrics from normalized APR data.

Outputs docs/data/viz_data.json with:
  - cities[]:        per-city aggregate metrics (ranking table)
  - projects[]:      per-city/year project rows (drilldown)
  - metadata:        run timestamp, data vintage, methodology note
"""

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline.config import (
    ADU_STATUTORY_DAYS,
    CITY_PORTALS,
    DOCS_DATA,
    FRICTION_WEIGHTS,
    PENINSULA_JURISDICTIONS,
    RHNA_6TH_CYCLE,
    RHNA_CYCLE_START,
    TIMELINE_CEILING_DAYS,
    APR_YEARS,
)

log = logging.getLogger(__name__)


# ── Public API ────────────────────────────────────────────────────────────────

def build_viz_data(df_a: pd.DataFrame, df_a2: pd.DataFrame) -> dict:
    """Main entry point. Returns the full viz_data dict."""
    log.info("Building city-level metrics ...")

    city_rows = []
    project_rows = []

    for city in PENINSULA_JURISDICTIONS:
        a = df_a[df_a["jurisdiction"] == city].copy()
        a2 = df_a2[df_a2["jurisdiction"] == city].copy()

        if a.empty and a2.empty:
            log.warning("No APR data found for %s — skipping", city)
            continue

        city_metrics = _city_metrics(city, a, a2)
        city_rows.append(city_metrics)

        projs = _project_rows(city, a, a2)
        project_rows.extend(projs)

    # Sort by friction score descending (worst first)
    city_rows.sort(key=lambda x: x["friction_score"], reverse=True)

    # Assign rank
    for i, c in enumerate(city_rows, 1):
        c["rank"] = i

    return {
        "metadata": _metadata(df_a, df_a2),
        "methodology": _methodology_note(),
        "cities": city_rows,
        "projects": project_rows,
    }


def write_viz_data(data: dict) -> Path:
    out = DOCS_DATA / "viz_data.json"
    with open(out, "w") as f:
        json.dump(data, f, indent=2, default=_json_serializer)
    log.info("Wrote %s (%.1f KB)", out, out.stat().st_size / 1024)
    return out


# ── City-level metrics ────────────────────────────────────────────────────────

def _city_metrics(city: str, a: pd.DataFrame, a2: pd.DataFrame) -> dict:
    rhna_target = RHNA_6TH_CYCLE.get(city, 0)

    # ── Funnel counts (all years combined) ───────────────────────────────────
    apps_total        = int(a["total_proposed_units"].fillna(1).clip(lower=1).sum()) if not a.empty else 0
    entitlements      = int(a2["total_approved_units"].fillna(a2.get("total_proposed_units", 0)).fillna(1).sum()) if not a2.empty else 0

    # Build permits: rows in A2 where building_permit date is populated OR
    # where the row has a non-null total_approved_units and no entitlement-only flag.
    # Conservative: count all A2 rows as "progressed through entitlement."
    # Separate building permit count from rows with permit date.
    a2_with_permit = a2[a2["date_building_permit"].notna()] if not a2.empty else pd.DataFrame()
    permits_dated   = int(a2_with_permit["total_approved_units"].fillna(1).sum())
    permits_total   = int(a2["total_approved_units"].fillna(1).sum()) if not a2.empty else 0

    a2_with_co      = a2[a2["date_certificate_of_occupancy"].notna()] if not a2.empty else pd.DataFrame()
    cos_total       = int(a2_with_co["total_approved_units"].fillna(1).sum())

    # 6th cycle only (2023+) for RHNA progress
    a2_6th = a2[a2["reporting_year"] >= RHNA_CYCLE_START] if not a2.empty else pd.DataFrame()
    rhna_permitted = int(a2_6th["total_approved_units"].fillna(1).sum())

    # ── ADU sub-metrics ───────────────────────────────────────────────────────
    adu_metrics = _adu_metrics(
        a[a["is_adu"]] if not a.empty else pd.DataFrame(),
        a2[a2["is_adu"]] if not a2.empty else pd.DataFrame(),
    )

    # ── Timeline medians ──────────────────────────────────────────────────────
    timeline = _timeline_metrics(a, a2)

    # ── Data completeness ─────────────────────────────────────────────────────
    date_completeness = _date_completeness(a, a2)

    # ── Friction score ────────────────────────────────────────────────────────
    friction = _friction_score(
        apps_total, permits_total, rhna_permitted, rhna_target,
        timeline["median_days_to_permit"]
    )

    # ── Data source ───────────────────────────────────────────────────────────
    portal = CITY_PORTALS.get(city, {})
    data_source = "hybrid" if portal else "hcd_apr"

    return {
        "city": city,
        "rhna_target": rhna_target,
        "rhna_permitted": rhna_permitted,
        "rhna_progress_pct": round(rhna_permitted / rhna_target * 100, 1) if rhna_target else None,
        "apps_total": apps_total,
        "entitlements_total": entitlements,
        "permits_total": permits_total,
        "cos_total": cos_total,
        "conversion_rate_pct": round(permits_total / apps_total * 100, 1) if apps_total else None,
        **timeline,
        "date_completeness_pct": date_completeness,
        "adu": adu_metrics,
        "friction_score": friction,
        "data_source": data_source,
        "portal_url": portal.get("url"),
        "portal_notes": portal.get("notes"),
        "years_in_data": _years_present(a, a2),
    }


# ── Timeline metrics ──────────────────────────────────────────────────────────

def _timeline_metrics(a: pd.DataFrame, a2: pd.DataFrame) -> dict:
    """
    Compute median days between pipeline stages.

    We join on (jurisdiction, address, unit_type) to pair Table A application
    dates with Table A2 permit dates. Match rate varies by city/year — we
    surface the match rate alongside the median so users understand reliability.
    """
    if a.empty or a2.empty:
        return _empty_timeline()

    join_keys = [k for k in ["jurisdiction", "address", "unit_type"] if k in a.columns and k in a2.columns]
    if not join_keys:
        return _empty_timeline()

    merged = a.merge(
        a2[join_keys + ["date_building_permit", "date_entitlement"]],
        on=join_keys,
        how="inner",
        suffixes=("_a", "_a2"),
    )

    if merged.empty:
        return _empty_timeline()

    # Days: application complete → entitlement
    merged["days_to_entitlement"] = (
        merged["date_entitlement"] - merged["date_application_complete"]
    ).dt.days

    # Days: application complete → building permit
    merged["days_to_permit"] = (
        merged["date_building_permit"] - merged["date_application_complete"]
    ).dt.days

    def safe_median(series: pd.Series) -> Any:
        s = series.dropna()
        s = s[(s >= 0) & (s <= 3650)]  # sanity: 0–10 years
        return int(s.median()) if len(s) >= 3 else None

    match_rate = round(len(merged) / len(a) * 100, 1) if len(a) else None

    return {
        "median_days_to_entitlement": safe_median(merged["days_to_entitlement"]),
        "median_days_to_permit": safe_median(merged["days_to_permit"]),
        "timeline_match_rate_pct": match_rate,
        "timeline_n": len(merged[merged["days_to_permit"].notna()]),
    }


def _empty_timeline() -> dict:
    return {
        "median_days_to_entitlement": None,
        "median_days_to_permit": None,
        "timeline_match_rate_pct": None,
        "timeline_n": 0,
    }


# ── ADU metrics ───────────────────────────────────────────────────────────────

def _adu_metrics(a_adu: pd.DataFrame, a2_adu: pd.DataFrame) -> dict:
    """
    ADU-specific metrics. Key accountability metric:
    % approved within 60-day statutory deadline.
    """
    apps = int(a_adu["total_proposed_units"].fillna(1).sum()) if not a_adu.empty else 0
    permits = int(a2_adu["total_approved_units"].fillna(1).sum()) if not a2_adu.empty else 0

    # Statutory compliance: join and check days ≤ 60
    if a_adu.empty or a2_adu.empty:
        return {
            "apps": apps,
            "permits": permits,
            "within_60_days_pct": None,
            "median_days": None,
            "statutory_violations_n": None,
        }

    join_keys = [k for k in ["jurisdiction", "address", "unit_type"]
                 if k in a_adu.columns and k in a2_adu.columns]
    if not join_keys:
        return {"apps": apps, "permits": permits,
                "within_60_days_pct": None, "median_days": None,
                "statutory_violations_n": None}

    merged = a_adu.merge(
        a2_adu[join_keys + ["date_building_permit"]],
        on=join_keys, how="inner",
    )
    merged["days"] = (
        merged["date_building_permit"] - merged["date_application_complete"]
    ).dt.days
    valid = merged["days"].dropna()
    valid = valid[(valid >= 0) & (valid <= 730)]

    if len(valid) < 2:
        return {"apps": apps, "permits": permits,
                "within_60_days_pct": None, "median_days": None,
                "statutory_violations_n": None}

    within = int((valid <= ADU_STATUTORY_DAYS).sum())
    violations = int((valid > ADU_STATUTORY_DAYS).sum())

    return {
        "apps": apps,
        "permits": permits,
        "within_60_days_pct": round(within / len(valid) * 100, 1),
        "median_days": int(valid.median()),
        "statutory_violations_n": violations,
        "statutory_sample_n": len(valid),
    }


# ── Project-level rows (drilldown) ────────────────────────────────────────────

def _project_rows(city: str, a: pd.DataFrame, a2: pd.DataFrame) -> list[dict]:
    """Return project-level rows for drilldown table."""
    if a.empty:
        return []

    rows = []
    for _, row in a.iterrows():
        year = row.get("reporting_year")
        rows.append({
            "city": city,
            "year": int(year) if pd.notna(year) else None,
            "address": _str(row.get("address")),
            "apn": _str(row.get("apn")),
            "unit_type": _str(row.get("unit_type")),
            "is_adu": bool(row.get("is_adu", False)),
            "proposed_units": _int(row.get("total_proposed_units")),
            "date_application_complete": _date(row.get("date_application_complete")),
            "date_entitlement": _date(row.get("date_entitlement")),
            "date_building_permit": _date(row.get("date_building_permit")),
            "date_co": _date(row.get("date_certificate_of_occupancy")),
            "streamlining": _str(row.get("streamlining")),
        })
    return rows


# ── Friction score ────────────────────────────────────────────────────────────

def _friction_score(
    apps: int,
    permits: int,
    rhna_permitted: int,
    rhna_target: int,
    median_days: Any,
) -> int:
    """
    0–100 score, higher = more friction.

    Components (see config.FRICTION_WEIGHTS):
      rhna_gap:        1 - (permits_6th_cycle / rhna_target)        weight 0.40
      conversion_gap:  1 - (permits / applications)                  weight 0.35
      timeline_score:  median_days_to_permit / TIMELINE_CEILING      weight 0.25

    When timeline data is unavailable, rhna_gap and conversion_gap are
    re-weighted proportionally (0.53 / 0.47).
    """
    w = FRICTION_WEIGHTS

    rhna_gap = 1.0 - min(rhna_permitted / rhna_target, 1.0) if rhna_target > 0 else 1.0
    conv_gap  = 1.0 - min(permits / apps, 1.0) if apps > 0 else 1.0

    if median_days is not None:
        timeline = min(median_days / TIMELINE_CEILING_DAYS, 1.0)
        score = (
            rhna_gap  * w["rhna_gap"] +
            conv_gap  * w["conversion_gap"] +
            timeline  * w["timeline_score"]
        ) * 100
    else:
        # Re-weight without timeline
        total_w = w["rhna_gap"] + w["conversion_gap"]
        score = (
            rhna_gap * w["rhna_gap"] / total_w +
            conv_gap * w["conversion_gap"] / total_w
        ) * 100

    return min(int(round(score)), 100)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _date_completeness(a: pd.DataFrame, a2: pd.DataFrame) -> dict:
    """Return % of rows with each date field populated."""
    result = {}
    for df, label in [(a, "table_a"), (a2, "table_a2")]:
        if df.empty:
            continue
        for col in ["date_application_complete", "date_entitlement",
                    "date_building_permit", "date_certificate_of_occupancy"]:
            if col in df.columns:
                pct = round(df[col].notna().mean() * 100, 1)
                result[f"{label}_{col}_pct"] = pct
    return result


def _years_present(a: pd.DataFrame, a2: pd.DataFrame) -> list[int]:
    years = set()
    for df in [a, a2]:
        if not df.empty and "reporting_year" in df.columns:
            years.update(df["reporting_year"].dropna().astype(int).tolist())
    return sorted(years)


def _metadata(df_a: pd.DataFrame, df_a2: pd.DataFrame) -> dict:
    max_year_a  = int(df_a["reporting_year"].max())  if not df_a.empty  else None
    max_year_a2 = int(df_a2["reporting_year"].max()) if not df_a2.empty else None
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "latest_apr_year_table_a": max_year_a,
        "latest_apr_year_table_a2": max_year_a2,
        "source": "HCD Housing Element Annual Progress Report (data.ca.gov)",
        "source_url": "https://data.ca.gov/dataset/housing-element-annual-progress-report-apr-data-by-jurisdiction-and-year",
        "jurisdiction_count": df_a["jurisdiction"].nunique() if not df_a.empty else 0,
    }


def _methodology_note() -> dict:
    w = FRICTION_WEIGHTS
    return {
        "friction_score": (
            f"Friction score (0–100, higher = more obstructive) is a weighted composite: "
            f"RHNA progress gap ({int(w['rhna_gap']*100)}% weight) + "
            f"application-to-permit conversion gap ({int(w['conversion_gap']*100)}% weight) + "
            f"median days to permit normalized to {TIMELINE_CEILING_DAYS}-day ceiling "
            f"({int(w['timeline_score']*100)}% weight). "
            f"When timeline data is unavailable (city did not report required dates to HCD), "
            f"the first two components are re-weighted proportionally."
        ),
        "timeline": (
            "Median days are computed by joining Table A application dates to Table A2 permit "
            "dates on (jurisdiction, address, unit type). Many cities leave date fields blank — "
            "this is itself a compliance failure under Government Code 65400."
        ),
        "adu_compliance": (
            f"ADU statutory deadline is {ADU_STATUTORY_DAYS} days under AB 68/881. "
            "Any city exceeding this for ADU applications is in documented violation of state law."
        ),
        "data_caveats": [
            "All data is self-reported by jurisdictions to HCD. HCD performs completeness checks but not field-level audits.",
            "Duplicate entries from resubmissions are deduplicated by keeping the row with the most date fields populated.",
            "RHNA progress uses 6th cycle (2023–2031) only. Pre-2023 permits counted toward 5th cycle.",
            "Projects appear in multiple APR years as they move through the pipeline; unit counts may double-count across years.",
        ],
    }


def _json_serializer(obj: Any) -> Any:
    if isinstance(obj, (pd.Timestamp, datetime, date)):
        return obj.isoformat()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if pd.isna(obj):
        return None
    raise TypeError(f"Not serializable: {type(obj)}")


def _str(val: Any) -> Any:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    return str(val).strip() or None


def _int(val: Any) -> Any:
    try:
        v = float(val)
        return int(v) if not np.isnan(v) else None
    except (TypeError, ValueError):
        return None


def _date(val: Any) -> Any:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    if isinstance(val, (pd.Timestamp, datetime)):
        return val.date().isoformat()
    return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from pipeline.fetch_apr import fetch_all
    a, a2 = fetch_all()
    data = build_viz_data(a, a2)
    path = write_viz_data(data)
    print(f"\nOutput: {path}")
    print(f"Cities: {len(data['cities'])}")
    print(f"Projects: {len(data['projects'])}")
    for c in data["cities"][:5]:
        print(f"  {c['rank']:2d}. {c['city']:<22} score={c['friction_score']:3d}  rhna={c['rhna_progress_pct']}%")
