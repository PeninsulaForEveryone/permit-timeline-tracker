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
from datetime import date, datetime, timezone
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
    HCD_COMPLIANCE_REPORT_URL,
    HCD_SMAP_DASHBOARD_URL,
    PENINSULA_JURISDICTIONS,
    RHNA_6TH_CYCLE,
    RHNA_CYCLE_START,
    TIMELINE_CEILING_DAYS,
)

log = logging.getLogger(__name__)


# ── Columns that live only in Table A2 (not Table A) ────────────────────────
# After normalization both DataFrames have ALL canonical columns (NA where absent).
# Merging A onto A2 would create _x/_y suffixes for these.
# We drop them from whichever side shouldn't own them before merging.
_A2_ONLY_DATE_COLS = ["date_entitlement", "date_building_permit", "date_certificate_of_occupancy"]
_A_ONLY_DATE_COLS  = ["date_application_submitted"]

def _prep_a_for_merge(df: pd.DataFrame) -> pd.DataFrame:
    """Drop A2-only date cols from a Table A slice before merging."""
    return df.drop(columns=[c for c in _A2_ONLY_DATE_COLS if c in df.columns], errors="ignore")

def _prep_a2_for_merge(df: pd.DataFrame, extra_cols: list) -> pd.DataFrame:
    """Select only join keys + requested cols from a Table A2 slice."""
    return df[extra_cols]  # caller already builds the right col list


# ── Public API ────────────────────────────────────────────────────────────────

def build_viz_data(df_a: pd.DataFrame, df_a2: pd.DataFrame,
                   compliance: dict | None = None) -> dict:
    """Main entry point. Returns the full viz_data dict.

    `compliance` is the HCD housing element compliance report keyed by
    jurisdiction (see fetch_compliance.fetch_compliance). It is optional: when
    absent, the housing element panel reports "unknown" rather than guessing.
    """
    log.info("Building city-level metrics ...")

    compliance = compliance or {}
    latest_year = _latest_reporting_year(df_a, df_a2)

    city_rows = []
    project_rows = []

    for city in PENINSULA_JURISDICTIONS:
        a = df_a[df_a["jurisdiction"] == city].copy()
        a2 = df_a2[df_a2["jurisdiction"] == city].copy()

        if a.empty and a2.empty:
            log.warning("No APR data found for %s — skipping", city)
            continue

        city_metrics = _city_metrics(city, a, a2, compliance.get(city), latest_year)
        city_rows.append(city_metrics)

        projs = _project_rows(city, a, a2)
        project_rows.extend(projs)

    # Sort by friction score descending (worst first)
    city_rows.sort(key=lambda x: x["friction_score"], reverse=True)

    # Assign rank
    for i, c in enumerate(city_rows, 1):
        c["rank"] = i

    return {
        "metadata": _metadata(df_a, df_a2, city_rows, latest_year),
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

def _city_metrics(city: str, a: pd.DataFrame, a2: pd.DataFrame,
                  compliance: dict | None, latest_year: int | None) -> dict:
    rhna_target = RHNA_6TH_CYCLE.get(city, 0)

    # ── Funnel counts ────────────────────────────────────────────────────────
    def _s(df, col):
        if df.empty or col not in df.columns:
            return 0
        return int(pd.to_numeric(df[col], errors="coerce").fillna(0).sum())

    # Table A: proposed units and entitlement approvals (discretionary applications only)
    apps_total   = _s(a, "total_proposed_units")
    entitlements = _s(a, "total_approved_units")   # TOT_APPROVED_UNITS in Table A

    # Table A2: deduplicate cross-year before counting (projects reappear each year)
    a2d = _dedup_cross_year(a2)
    permits_total = _s(a2d, "total_approved_units")   # NO_BUILDING_PERMITS in Table A2
    cos_total     = _s(
        a2d[a2d["date_certificate_of_occupancy"].notna()] if not a2d.empty else pd.DataFrame(),
        "total_approved_units"
    )

    # RHNA progress: 6th cycle permits using actual permit date year where available
    a2_rhna = _rhna_slice(a2d, RHNA_CYCLE_START)
    rhna_permitted = _s(a2_rhna, "total_approved_units")

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
    friction = _friction_score(rhna_permitted, rhna_target, timeline["median_days_to_permit"])

    # ── Data source ───────────────────────────────────────────────────────────
    portal = CITY_PORTALS.get(city, {})
    data_source = "hybrid" if portal else "hcd_apr"

    # ── Reporting coverage ────────────────────────────────────────────────────
    # Jurisdictions file APRs late and HCD posts them as they arrive, so the most
    # recent year is always partial. Surface which years THIS city actually filed
    # rather than implying every city is current through the dataset's max year.
    years = _years_present(a, a2)
    city_latest = max(years) if years else None

    return {
        "city": city,
        "rhna_target": rhna_target,
        "rhna_permitted": rhna_permitted,
        "rhna_progress_pct": round(rhna_permitted / rhna_target * 100, 1) if rhna_target else None,
        "apps_total": apps_total,
        "entitlements_total": entitlements,
        "permits_total": permits_total,
        "cos_total": cos_total,
        "entitlement_rate_pct": min(round(entitlements / apps_total * 100, 1), 100.0) if apps_total else None,
        **timeline,
        "date_completeness_pct": date_completeness,
        "adu": adu_metrics,
        "friction_score": friction,
        "data_source": data_source,
        "portal_url": portal.get("url"),
        "portal_notes": portal.get("notes"),
        "years_in_data": years,
        "latest_reported_year": city_latest,
        "filed_latest_apr": (city_latest == latest_year) if city_latest and latest_year else None,
        "housing_element": _housing_element(compliance),
    }


# ── Housing element compliance ────────────────────────────────────────────────

def _housing_element(rec: dict | None) -> dict:
    """Pass through HCD's compliance finding — no derived risk tiers.

    Builder's Remedy exposure is a legal status HCD determines, not something
    that can be inferred from permit counts. When a jurisdiction's element is
    out of compliance, Gov. Code 65589.5(d)(5) removes the city's ability to
    deny a qualifying affordable project for inconsistency with its zoning or
    general plan; when it is in compliance, the remedy does not apply, however
    slow the city is. So we report the finding and its date, and nothing else.
    """
    if not rec:
        return {
            "status": None,
            "in_compliance": None,
            "reviewed_date": None,
            "record_type": None,
            "review_status": None,
            "builders_remedy_applies": None,
        }
    return {
        "status": rec.get("compliance_status"),
        "in_compliance": rec.get("in_compliance"),
        "reviewed_date": rec.get("reviewed_date"),
        "record_type": rec.get("record_type"),
        "review_status": rec.get("review_status"),
        # Out of compliance ⇒ the remedy is available to qualifying projects.
        "builders_remedy_applies": (
            None if rec.get("in_compliance") is None else not rec["in_compliance"]
        ),
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

    a_left   = _prep_a_for_merge(_dedup_cross_year(a))
    a2_right = _consolidate_dates_a2(a2, join_keys, ["date_building_permit", "date_entitlement"])
    merged   = a_left.merge(a2_right, on=join_keys, how="inner")

    if merged.empty:
        return _empty_timeline()

    # Days: application complete → entitlement
    merged["days_to_entitlement"] = (
        merged["date_entitlement"] - merged["date_application_submitted"]
    ).dt.days

    # Days: application complete → building permit
    merged["days_to_permit"] = (
        merged["date_building_permit"] - merged["date_application_submitted"]
    ).dt.days

    def safe_median(series: pd.Series) -> Any:
        s = series.dropna()
        s = s[(s > 0) & (s <= 3650)]
        if len(s) < 3:
            return None
        s = s[s <= s.quantile(0.98)]  # trim top 2% to reduce outlier distortion
        m = int(round(s.median()))
        return m if m > 0 else None

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

    a_left   = _prep_a_for_merge(_dedup_cross_year(a_adu))
    a2_right = _consolidate_dates_a2(a2_adu, join_keys, ["date_building_permit"])
    merged   = a_left.merge(a2_right, on=join_keys, how="inner")
    merged["days"] = (
        merged["date_building_permit"] - merged["date_application_submitted"]
    ).dt.days
    valid = merged["days"].dropna()
    valid = valid[(valid > 0) & (valid <= 730)]   # >0: filter same-day artifacts

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
            "date_application_submitted": _date(row.get("date_application_submitted")),
            "date_entitlement": _date(row.get("date_entitlement")),
            "date_building_permit": _date(row.get("date_building_permit")),
            "date_co": _date(row.get("date_certificate_of_occupancy")),
            "streamlining": _str(row.get("streamlining")),
        })
    return rows


# ── Friction score ────────────────────────────────────────────────────────────

def _friction_score(rhna_permitted: int, rhna_target: int, median_days: Any) -> int:
    """
    0–100 score, higher = more friction. Weights come from config.FRICTION_WEIGHTS
    so the score and the methodology note on the page cannot drift apart.

    Components:
      rhna_gap       = 1 - (building_permits_6th_cycle / rhna_target)
      timeline_score = median days (submittal → building permit) / TIMELINE_CEILING

    Cross-table conversion rate removed: Table A (discretionary applications)
    and Table A2 (all permits incl. ministerial) are not comparable denominators.
    When timeline data unavailable, full weight goes to rhna_gap.
    """
    w_rhna = FRICTION_WEIGHTS["rhna_gap"]
    w_time = FRICTION_WEIGHTS["timeline_score"]

    rhna_gap = 1.0 - min(rhna_permitted / rhna_target, 1.0) if rhna_target > 0 else 1.0

    if median_days is not None:
        timeline = min(median_days / TIMELINE_CEILING_DAYS, 1.0)
        score = (rhna_gap * w_rhna + timeline * w_time) / (w_rhna + w_time) * 100
    else:
        score = rhna_gap * 100

    return min(int(round(score)), 100)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _dedup_cross_year(a2: pd.DataFrame) -> pd.DataFrame:
    """Deduplicate Table A2 across reporting years for unit counting.
    Projects reappear each year they are active; keep the row with the most dates."""
    if a2.empty:
        return a2
    key = [c for c in ["jurisdiction", "address", "unit_type"] if c in a2.columns]
    if not key:
        return a2
    a2 = a2.copy()
    date_cols = ["date_entitlement", "date_building_permit", "date_certificate_of_occupancy"]
    a2["_dc"] = a2[[c for c in date_cols if c in a2.columns]].notna().sum(axis=1)
    a2 = a2.sort_values("_dc", ascending=False).drop_duplicates(subset=key, keep="first")
    return a2.drop(columns=["_dc"])


def _consolidate_dates_a2(a2: pd.DataFrame, join_keys: list, date_cols: list) -> pd.DataFrame:
    """Collapse cross-year A2 rows into one per project, taking the earliest
    non-null value for each date column. This recovers dates that HCD splits
    across reporting years (e.g. bp_date in year N, co_date in year N+1)."""
    if a2.empty:
        return a2
    present = [c for c in date_cols if c in a2.columns]
    keys_present = [k for k in join_keys if k in a2.columns]
    if not keys_present or not present:
        return a2
    return a2.groupby(keys_present, as_index=False)[present].min()


def _rhna_slice(a2_deduped: pd.DataFrame, cycle_start: int) -> pd.DataFrame:
    """Return A2 rows counting toward current RHNA cycle.
    Uses actual BP issue date year when available; falls back to reporting year."""
    if a2_deduped.empty:
        return a2_deduped
    df = a2_deduped.copy()
    if "date_building_permit" in df.columns:
        bp_year = pd.to_datetime(df["date_building_permit"], errors="coerce").dt.year
        rep_year = pd.to_numeric(df.get("reporting_year", pd.Series(dtype=float)), errors="coerce")
        eff_year = bp_year.combine_first(rep_year)
    else:
        eff_year = pd.to_numeric(df.get("reporting_year", pd.Series(dtype=float)), errors="coerce")
    return df[eff_year >= cycle_start]


def _date_completeness(a: pd.DataFrame, a2: pd.DataFrame) -> dict:
    """Return % of rows with each date field populated."""
    result = {}
    for df, label in [(a, "table_a"), (a2, "table_a2")]:
        if df.empty:
            continue
        for col in ["date_application_submitted", "date_entitlement",
                    "date_building_permit", "date_certificate_of_occupancy"]:
            if col in df.columns:
                pct = round(df[col].notna().mean() * 100, 1)
                result[f"{label}_{col}_pct"] = pct
    return result


def _latest_reporting_year(df_a: pd.DataFrame, df_a2: pd.DataFrame) -> Any:
    """Newest reporting year present anywhere in the dataset."""
    years = []
    for df in (df_a, df_a2):
        if not df.empty and "reporting_year" in df.columns:
            y = pd.to_numeric(df["reporting_year"], errors="coerce").max()
            if pd.notna(y):
                years.append(int(y))
    return max(years) if years else None


def _years_present(a: pd.DataFrame, a2: pd.DataFrame) -> list[int]:
    years = set()
    for df in [a, a2]:
        if not df.empty and "reporting_year" in df.columns:
            years.update(df["reporting_year"].dropna().astype(int).tolist())
    return sorted(years)


def _metadata(df_a: pd.DataFrame, df_a2: pd.DataFrame,
              city_rows: list[dict], latest_year: Any) -> dict:
    max_year_a  = int(df_a["reporting_year"].max())  if not df_a.empty  else None
    max_year_a2 = int(df_a2["reporting_year"].max()) if not df_a2.empty else None

    # APRs for calendar year N are due to HCD by April 1 of year N+1, and HCD
    # posts them as they arrive — so the newest year in the file is always
    # partial. Count who has actually filed it instead of implying full coverage.
    filed = [c["city"] for c in city_rows if c.get("filed_latest_apr")]
    not_filed = sorted(c["city"] for c in city_rows if c.get("filed_latest_apr") is False)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "latest_apr_year": latest_year,
        "latest_apr_year_table_a": max_year_a,
        "latest_apr_year_table_a2": max_year_a2,
        "latest_year_filed_count": len(filed),
        "latest_year_not_filed": not_filed,
        "complete_apr_year": (latest_year - 1) if latest_year else None,
        "source": "HCD Housing Element Annual Progress Report (data.ca.gov)",
        "source_url": "https://data.ca.gov/dataset/housing-element-annual-progress-report-apr-data-by-jurisdiction-and-year",
        "compliance_source": "HCD Housing Element Compliance Report (data.ca.gov)",
        "compliance_source_url": HCD_COMPLIANCE_REPORT_URL,
        "smap_dashboard_url": HCD_SMAP_DASHBOARD_URL,
        "jurisdiction_count": df_a["jurisdiction"].nunique() if not df_a.empty else 0,
    }


def _methodology_note() -> dict:
    w = FRICTION_WEIGHTS
    pct = {k: int(round(v * 100)) for k, v in w.items()}
    return {
        "friction_score": (
            f"Friction score (0–100, higher = more obstructive) is a weighted composite of "
            f"two things: the RHNA progress gap ({pct['rhna_gap']}% weight) — the share of the "
            f"city's 6th-cycle target it has not yet permitted — and median days from application "
            f"submittal to building permit, normalized to a {TIMELINE_CEILING_DAYS}-day ceiling "
            f"({pct['timeline_score']}% weight). When a city reported no usable dates to HCD, the "
            f"timeline term drops out and the score is the RHNA gap alone. Nothing else feeds it: "
            f"it is not a legal determination and carries no regulatory meaning."
        ),
        "rhna_progress": (
            "RHNA progress counts BUILDING PERMITS ISSUED, not entitlements or approvals. That is "
            "HCD's own accounting rule — a jurisdiction only gets RHNA credit when a building "
            "permit is issued (APR Table A2), so an entitled-but-unpermitted project counts for "
            "nothing here, exactly as it counts for nothing with the state. The separate "
            "'entitlement rate' figure is a different measure drawn from Table A: the share of "
            "proposed units in submitted applications that the city approved. The two are not "
            "stages of one funnel — Table A covers applications requiring discretionary review, "
            "while Table A2 counts every building permit including ministerial ADUs that never "
            "appear in Table A."
        ),
        "timeline": (
            "The clock starts at APP_SUBMIT_DT in APR Table A — the date the application was "
            "submitted — and stops at BP_ISSUE_DT1 (building permit) or ENT_APPROVE_DT1 "
            "(entitlement) in Table A2, matched on (jurisdiction, address, unit type). "
            "IMPORTANT: everything before submittal is invisible in this data. The APR schema has "
            "no field for first contact with the city, no field for an SB 330 preliminary "
            "application, and no field for the date an application was deemed complete. A city "
            "that spends a year refusing to deem a pre-application or application complete, then "
            "moves fast afterward, looks identical here to a genuinely fast city — and a city "
            "that accepts a filing early and does its work in the open looks slower than it is. "
            "Read these medians as time-in-the-formal-process, not total time to get a permit. "
            "Match rate is reported per city so you can see how much of the pipeline the medians "
            "actually cover."
        ),
        "builders_remedy": (
            "Builder's Remedy exposure and SB 423/SB 35 streamlining are triggered by housing "
            "element compliance, which HCD determines — not by anything in the permit data. This "
            "page reports HCD's published finding for each jurisdiction verbatim, with the date "
            "HCD completed its review, from the Housing Element Compliance Report on data.ca.gov. "
            "Under Gov. Code 65589.5(d)(5), a jurisdiction whose element is out of substantial "
            "compliance cannot deny a qualifying project with at least 20% lower-income units (or "
            "100% moderate) for inconsistency with its zoning or general plan. SB 423 streamlining "
            "additionally depends on HCD's annual production determination, published on HCD's "
            "SMAP dashboard; that determination is not reproduced here."
        ),
        "adu_compliance": (
            f"ADU statutory deadline is {ADU_STATUTORY_DAYS} days under AB 68/881. Because the "
            "APR clock starts at submittal, a median above 60 days is evidence of a permitting "
            "process that exceeds the ministerial deadline, but it is not by itself proof of a "
            "violation in any individual case — the statutory clock can be reset by a resubmittal."
        ),
        "data_caveats": [
            "All data is self-reported by jurisdictions to HCD. HCD performs completeness checks but not field-level audits.",
            "APRs for a calendar year are due to HCD by April 1 of the following year, and HCD publishes them as they are filed. The most recent year in this dataset is therefore always partial — the source note below records how many jurisdictions have filed it, and each city's card shows the latest year it reported.",
            "Duplicate entries from resubmissions are deduplicated by keeping the row with the most date fields populated.",
            "RHNA progress uses 6th cycle (2023–2031) only, counted by building permit issue date where reported. Pre-2023 permits counted toward the 5th cycle.",
            "Projects appear in multiple APR years as they move through the pipeline; the pipeline deduplicates cross-year on (jurisdiction, address, unit type) to avoid double-counting.",
            "Early-cycle bias: the friction score is dominated by RHNA progress (60% weight), which structurally disadvantages large cities with ambitious targets in the first years of an 8-year cycle. A city like Daly City (RHNA target: 7,169) must permit ~900 units per year just to stay on pace, while a small city like Portola Valley needs only ~28. Scores for large cities will improve substantially as the cycle matures — interpret high scores for large cities with this in mind.",
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
