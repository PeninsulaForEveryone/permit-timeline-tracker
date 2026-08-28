"""
Central configuration for the Peninsula Permit Tracker pipeline.

Data sources:
  - HCD APR Table A (applications): https://data.ca.gov/dataset/81b0841f-2802-403e-b48e-2ef4b751f77c
  - HCD APR Table A2 (permits/entitlements): same dataset, different resource

IMPORTANT: data.ca.gov resource URLs occasionally change. If a fetch fails,
the CKAN API fallback in fetch_apr.py will auto-discover the current URL.
Last verified: 2025-05.
"""

from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"
DOCS_DATA = ROOT / "docs" / "data"

for p in [DATA_RAW, DATA_PROCESSED, DOCS_DATA]:
    p.mkdir(parents=True, exist_ok=True)

# ── HTTP ─────────────────────────────────────────────────────────────────────

REQUEST_TIMEOUT = 60
HEADERS = {"User-Agent": "PeninsulaPermitTracker/1.0 (https://github.com/PeninsulaForEveryone)"}

# ── HCD APR source URLs ───────────────────────────────────────────────────────

# CKAN dataset ID (stable)
HCD_APR_CKAN_DATASET_ID = "81b0841f-2802-403e-b48e-2ef4b751f77c"
HCD_APR_CKAN_API = "https://data.ca.gov/api/3/action/package_show"

# Direct CSV URLs (faster; fall back to CKAN discovery if these 404)
HCD_TABLE_A_URL = (
    "https://data.ca.gov/dataset/81b0841f-2802-403e-b48e-2ef4b751f77c"
    "/resource/c78b769d-cc02-4050-91ef-79ded665b5a8/download/tablea.csv"
)
HCD_TABLE_A2_URL = (
    "https://data.ca.gov/dataset/81b0841f-2802-403e-b48e-2ef4b751f77c"
    "/resource/fe505d9b-8c36-42ba-ba30-08bc4f34e022/download/tablea2.csv"
)

# Cache filenames
TABLE_A_RAW = DATA_RAW / "hcd_apr_table_a.csv"
TABLE_A2_RAW = DATA_RAW / "hcd_apr_table_a2.csv"

# ── HCD Housing Element Compliance Report ─────────────────────────────────────
# Separate dataset from the APR. This is the authoritative record of whether a
# jurisdiction's housing element is in substantial compliance with state law —
# the actual legal trigger for the Builder's Remedy (Gov. Code 65589.5(d)(5))
# and for SB 423/SB 35 streamlining. It is NOT derivable from APR production data.

HCD_COMPLIANCE_CKAN_DATASET_ID = "55537b9f-0c54-456d-b76a-90c157718975"
HCD_COMPLIANCE_URL = (
    "https://data.ca.gov/dataset/55537b9f-0c54-456d-b76a-90c157718975"
    "/resource/2dcd1cd4-1348-4fc5-9c9c-219f82daac00/download/housing_element.csv"
)
COMPLIANCE_RAW = DATA_RAW / "hcd_housing_element_compliance.csv"

# HCD's public compliance dashboards (linked from the UI)
HCD_COMPLIANCE_REPORT_URL = (
    "https://www.hcd.ca.gov/housing-open-data-tools/housing-element-review-compliance-report"
)
HCD_SMAP_DASHBOARD_URL = (
    "https://www.hcd.ca.gov/planning-and-community-development"
    "/streamlined-ministerial-approval-process-dashboard"
)

# ── APR column name normalization ─────────────────────────────────────────────
# HCD has changed column names across APR years. We normalize on load.
# Keys = our canonical names; values = list of known source variants.

COLUMN_ALIASES = {
    # Canonical name          Real CSV columns, most-specific first
    "jurisdiction": ["JURIS_NAME", "Jurisdiction Name", "JurisdictionName", "Jurisdiction"],
    "reporting_year": ["YEAR", "Reporting Year", "ReportingYear", "Calendar Year"],
    "project_name": ["PROJECT_NAME", "Project Name", "ProjectName"],
    "address": ["STREET_ADDRESS", "Street Address", "StreetAddress", "Site Address"],
    "apn": ["APN", "Assessor Parcel Number"],
    "unit_category": [
        "UNIT_CAT",
        "Unit Category Type", "UnitCategoryType", "Project Type Category", "Unit Type",
    ],
    "total_proposed_units": [
        "TOT_PROPOSED_UNITS", "PROJ_UNITS", "TOTAL_UNITS", "TOTAL_PROPOSED_UNITS",
        "Total Proposed Units", "TotalProposedUnits", "Proposed Units",
    ],
    "total_approved_units": [
        # Table A uses TOT_APPROVED_UNITS; Table A2 uses NO_BUILDING_PERMITS
        "NO_BUILDING_PERMITS", "TOT_APPROVED_UNITS", "APPR_UNITS", "TOTAL_APPROVED_UNITS",
        "Total Approved Units", "TotalApprovedUnits", "Approved Units",
    ],
    "entitlement_units": [
        "NO_ENTITLEMENTS",
        "Entitlement Units", "EntitlementUnits",
    ],
    # NOTE: HCD Table A reports only the date the application was SUBMITTED
    # (APP_SUBMIT_DT). There is no "deemed complete" field, no SB 330 pre-application
    # field, and no first-contact field anywhere in the APR schema — so every
    # timeline in this project starts at submittal. Do not alias a deemed-complete
    # column into this field; that would silently mix two different milestones.
    "date_application_submitted": [
        "APP_SUBMIT_DT",
        "Date Application Submitted", "DateApplicationSubmitted",
        "Application Submittal Date", "Date of Application",
    ],
    "date_entitlement": [
        "ENT_APPROVE_DT1", "ENT_APPR_DT", "ENTITLE_DT", "ENT_DT",
        "Date Entitlement Approved", "DateEntitlementApproved",
        "Entitlement Date Issued", "Date Entitled",
    ],
    "date_building_permit": [
        "BP_ISSUE_DT1", "BP_DT", "BLDG_PERMIT_DT", "BP_ISSUE_DT",
        "Date Building Permit Issued", "DateBuildingPermitIssued",
        "Building Permit Date Issued", "BP Issue Date",
    ],
    "date_certificate_of_occupancy": [
        "CO_ISSUE_DT1", "CO_DT", "CERT_OCC_DT",
        "Date Certificate of Occupancy Issued", "DateCertificateofOccupancyIssued",
        "CO Date Issued", "Certificate of Occupancy Date",
    ],
    "streamlining": [
        "STREAMLINING", "STREAMLINE_TYPE",
        "Streamlining Provision", "StreamliningProvision", "Streamlining Application",
    ],
    "very_low_income_units": [
        "VLOW_INCOME_DR", "VLOW_INCOME_NDR",
        "Very Low Income Units", "VLI Units",
    ],
    "low_income_units": [
        "LOW_INCOME_DR", "LOW_INCOME_NDR",
        "Low Income Units", "LI Units",
    ],
    "moderate_income_units": [
        "MOD_INCOME_DR", "MOD_INCOME_NDR",
        "Moderate Income Units", "Mod Units",
    ],
    "above_moderate_units": [
        "ABOVE_MOD_INCOME", "ABOVE_MOD_UNITS", "ABVMOD_UNITS",
        "Above Moderate Income Units", "AMI Units",
    ],
}

# ── Unit category classification ──────────────────────────────────────────────

ADU_KEYWORDS = {"adu", "jadu", "accessory dwelling", "junior accessory"}

UNIT_CATEGORY_MAP = {
    "adu": "ADU",
    "jadu": "JADU",
    "accessory dwelling unit": "ADU",
    "junior accessory dwelling unit": "JADU",
    "single family residential": "SFR",
    "single family": "SFR",
    "sfr": "SFR",
    "multifamily": "Multifamily",
    "multi-family": "Multifamily",
    "multi family": "Multifamily",
    "mixed use": "Mixed Use",
    "mobile home": "Mobile Home",
    "other": "Other",
}

def classify_unit_type(raw: str) -> str:
    """Normalize raw unit category to one of: ADU, JADU, SFR, Multifamily, Mixed Use, Other."""
    if raw is None or (hasattr(raw, '__class__') and raw.__class__.__name__ == 'NAType'):
        return "Other"
    if str(raw).strip().lower() in ("", "nan", "none"):
        return "Other"
    s = str(raw).strip().lower()
    for keyword, canonical in UNIT_CATEGORY_MAP.items():
        if keyword in s:
            return canonical
    return "Other"

def is_adu(unit_type: str) -> bool:
    return unit_type in ("ADU", "JADU")

# ── Peninsula jurisdictions ───────────────────────────────────────────────────
# San Mateo County cities + Palo Alto (Santa Clara County).
# APR reports these by exact jurisdiction name — spelling must match HCD data.

PENINSULA_JURISDICTIONS = [
    "Atherton",
    "Belmont",
    "Brisbane",
    "Burlingame",
    "Colma",
    "Daly City",
    "East Palo Alto",
    "Foster City",
    "Half Moon Bay",
    "Hillsborough",
    "Menlo Park",
    "Millbrae",
    "Pacifica",
    "Palo Alto",          # Santa Clara County but geographically Peninsula
    "Portola Valley",
    "Redwood City",
    "San Bruno",
    "San Carlos",
    "San Mateo",
    "South San Francisco",
    "Woodside",
    # San Mateo County unincorporated (appears as county in APR)
    "San Mateo County",
]

# Canonical display names (for any APR spelling variants)
JURISDICTION_DISPLAY = {j: j for j in PENINSULA_JURISDICTIONS}
JURISDICTION_DISPLAY["San Mateo"] = "San Mateo"  # distinguish from county

# ── 6th Cycle RHNA targets (2023–2031) ───────────────────────────────────────
# Source: ABAG/HCD Final RHNA Determination, adopted January 2023.
# Total units across all income levels.

RHNA_6TH_CYCLE = {
    "Atherton": 348,
    "Belmont": 2247,
    "Brisbane": 720,
    "Burlingame": 2468,
    "Colma": 140,
    "Daly City": 7169,
    "East Palo Alto": 2440,
    "Foster City": 2697,
    "Half Moon Bay": 539,
    "Hillsborough": 838,
    "Menlo Park": 3081,
    "Millbrae": 2187,
    "Pacifica": 3080,
    "Palo Alto": 6086,
    "Portola Valley": 225,
    "Redwood City": 5765,
    "San Bruno": 3151,
    "San Carlos": 2367,
    "San Mateo": 7178,
    "South San Francisco": 4994,
    "Woodside": 281,
    "San Mateo County": 2347,  # unincorporated
}

# ── City open-data portal metadata ───────────────────────────────────────────

CITY_PORTALS = {
    "Redwood City": {
        "url": "https://www.redwoodcity.org/departments/community-development-department/maps-gis-property-research",
        "type": "arcgis",
        "arcgis_service": "https://services1.arcgis.com/RpFJKLpRCyj3qRJx/arcgis/rest/services",
        "notes": "Building permits layer available for download as CSV",
    },
    "Menlo Park": {
        "url": "https://data.menlopark.org",
        "type": "arcgis_hub",
        "notes": "ArcGIS Hub; permit layer present",
    },
    "Palo Alto": {
        "url": "https://www.paloalto.gov/Departments/Planning-Development-Services/Development-Services/Palo-Alto-Permit-View",
        "type": "permit_view",
        "notes": "Record-level search only; no bulk export API",
    },
    "San Mateo": {
        "url": "https://www.cityofsanmateo.org/4294/Online-Permit-Center",
        "type": "css_portal",
        "notes": "Custom portal; no bulk export",
    },
}

# ── Friction score weights ────────────────────────────────────────────────────
# Documented here so the methodology note in the UI is auto-generated from source.

# These are the ONLY weights used; transform._friction_score() reads them directly
# and transform._methodology_note() renders the published description from them,
# so the number on the page can never drift from the number in the code.
FRICTION_WEIGHTS = {
    "rhna_gap": 0.60,          # 1 - (building_permits_6th_cycle / rhna_target)
    "timeline_score": 0.40,    # median_days_to_permit / TIMELINE_CEILING
}
TIMELINE_CEILING_DAYS = 800    # anything ≥ this scores 1.0 on timeline dimension

# ADU-specific: state law mandates ministerial approval within 60 days
ADU_STATUTORY_DAYS = 60

# ── Reporting years in scope ──────────────────────────────────────────────────
# No year filter is applied: we ingest every reporting year HCD publishes and
# surface the per-city latest reported year in viz_data.json instead, because
# jurisdictions file late and coverage of the most recent year is always partial.

RHNA_CYCLE_START = 2023               # 6th cycle begins; pre-2023 = 5th cycle
