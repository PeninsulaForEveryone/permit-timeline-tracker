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

# ── APR column name normalization ─────────────────────────────────────────────
# HCD has changed column names across APR years. We normalize on load.
# Keys = our canonical names; values = list of known source variants.

COLUMN_ALIASES = {
    "jurisdiction": ["Jurisdiction Name", "JurisdictionName", "Jurisdiction"],
    "reporting_year": ["Reporting Year", "ReportingYear", "Calendar Year"],
    "project_name": ["Project Name", "ProjectName"],
    "address": ["Street Address", "StreetAddress", "Site Address"],
    "apn": ["APN", "Assessor Parcel Number"],
    "unit_category": [
        "Unit Category Type",
        "UnitCategoryType",
        "Project Type Category",
        "Unit Type",
    ],
    "total_proposed_units": [
        "Total Proposed Units",
        "TotalProposedUnits",
        "Proposed Units",
    ],
    "total_approved_units": [
        "Total Approved Units",
        "TotalApprovedUnits",
        "Approved Units",
    ],
    "date_application_complete": [
        "Date Application Deemed Complete",
        "DateApplicationDeemedComplete",
        "Application Complete Date",
        "Date of Application",
    ],
    "date_entitlement": [
        "Date Entitlement Approved",
        "DateEntitlementApproved",
        "Entitlement Date Issued",
        "Date Entitled",
    ],
    "date_building_permit": [
        "Date Building Permit Issued",
        "DateBuildingPermitIssued",
        "Building Permit Date Issued",
        "BP Issue Date",
    ],
    "date_certificate_of_occupancy": [
        "Date Certificate of Occupancy Issued",
        "DateCertificateofOccupancyIssued",
        "CO Date Issued",
        "Certificate of Occupancy Date",
    ],
    "streamlining": [
        "Streamlining Provision",
        "StreamliningProvision",
        "Streamlining Application",
    ],
    "very_low_income_units": ["Very Low Income Units", "VLI Units"],
    "low_income_units": ["Low Income Units", "LI Units"],
    "moderate_income_units": ["Moderate Income Units", "Mod Units"],
    "above_moderate_units": ["Above Moderate Income Units", "AMI Units"],
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

FRICTION_WEIGHTS = {
    "rhna_gap": 0.40,          # 1 - (permits_issued / rhna_target)
    "conversion_gap": 0.35,    # 1 - (permits / applications)
    "timeline_score": 0.25,    # median_days_to_permit / TIMELINE_CEILING
}
TIMELINE_CEILING_DAYS = 800    # anything ≥ this scores 1.0 on timeline dimension

# ADU-specific: state law mandates ministerial approval within 60 days
ADU_STATUTORY_DAYS = 60

# ── Reporting years in scope ──────────────────────────────────────────────────

APR_YEARS = list(range(2018, 2025))   # 2018–2024 inclusive
RHNA_CYCLE_START = 2023               # 6th cycle begins; pre-2023 = 5th cycle
