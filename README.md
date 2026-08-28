# Peninsula Permit Tracker

**How long does your city take to approve housing?**
Ranked by friction score — median permit approval time, pipeline conversion rates, RHNA progress,
and ADU statutory compliance — across San Mateo County and Peninsula jurisdictions.

Live: **https://peninsulaforeveryone.github.io/peninsula-permit-tracker/**

A project of [Peninsula for Everyone](https://www.peninsulaforeveryone.org).

---

## What this shows

| Metric | What it measures |
|---|---|
| Friction score (0–100) | Weighted composite: RHNA gap (60%) + timeline (40%) |
| RHNA progress | % of 6th-cycle (2023–2031) target met by **building permits issued** — HCD's own accounting rule; entitlements earn no RHNA credit |
| Entitlement rate | Units approved as a share of units proposed, in applications requiring discretionary review (Table A) — a separate measure, not the next stage of RHNA progress |
| Median days | **Application submitted** (Table A `APP_SUBMIT_DT`) → building permit issued (Table A2) |
| Housing element status | HCD's published compliance finding, which is what actually triggers the Builder's Remedy — fetched from HCD, never inferred from permit data |
| ADU compliance | % of ADU permits issued within 60-day statutory deadline (AB 68/881) |

### What the timeline does *not* include

The APR schema has no field for first contact with the city, no field for an SB 330
preliminary application, and **no field for the date an application was deemed complete**.
Every clock here therefore starts at submittal. A city that drags out pre-application review
or refuses to deem an application complete, then moves quickly once the clock starts, looks
fast in this data. Read the medians as time inside the formal process, not total time to
get a permit.

Cities that don't report required date fields to HCD are flagged — missing data is itself a compliance failure under Government Code 65400.

## Data sources

| Source | What | Years | URL |
|---|---|---|---|
| HCD Housing Element APR Table A | Applications submitted | 2018–present | [data.ca.gov](https://data.ca.gov/dataset/housing-element-annual-progress-report-apr-data-by-jurisdiction-and-year) |
| HCD Housing Element APR Table A2 | Entitlements, permits, COs | 2018–present | same |
| HCD Housing Element Compliance Report | Housing element compliance status per jurisdiction | current | [data.ca.gov](https://data.ca.gov/dataset/housing-element-compliance-report) |
| Redwood City Open Data | Building permits with dates | 2018–present | [redwoodcity.org](https://www.redwoodcity.org/departments/community-development-department/maps-gis-property-research) |
| Menlo Park ArcGIS Hub | Permit layer | 2018–present | [data.menlopark.org](https://data.menlopark.org) |

## Quickstart

```bash
git clone https://github.com/PeninsulaForEveryone/peninsula-permit-tracker
cd peninsula-permit-tracker
make install
make run
# open docs/index.html in a browser
```

Downloads ~50 MB of raw APR data, filters to Peninsula jurisdictions,
computes metrics, writes `docs/data/viz_data.json`.

## Pipeline

```
pipeline/
  config.py       URLs, constants, RHNA targets, friction score weights
  fetch_apr.py        Download & cache HCD APR Table A and A2; normalize columns
  fetch_compliance.py Download HCD housing element compliance status per jurisdiction
  transform.py        Compute per-city metrics, ADU sub-metrics, friction score
  run_all.py          Orchestrator: --force to re-download, --step for single steps

tools/
  make_og_card.py     Render docs/img.png, the 1200×630 social preview card
                      (needs Pillow; run after scores change)
```

Raw files cached in `data/raw/` (gitignored). Final JSON (`docs/data/viz_data.json`) is committed and served by GitHub Pages.

## Friction score methodology

```
score = RHNA gap (60%) + timeline score (40%)

RHNA gap        = 1 − (building_permits_6th_cycle / rhna_target)
Timeline score  = median days (submittal → building permit) / 800-day ceiling
```

Weights live in `config.FRICTION_WEIGHTS`; both the score and the methodology text published
in `viz_data.json` are generated from them, so the published description cannot drift from
the computation. The earlier application-to-permit conversion term was dropped because Table A
(discretionary applications) and Table A2 (all permits, including ministerial ADUs) are not
comparable denominators.

When a city doesn't report usable date fields, the timeline term drops out and the score is
the RHNA gap alone. The score is an editorial measure — it is not a legal determination and
carries no regulatory meaning.

## Builder's Remedy and SB 423

Both are triggered by **housing element compliance**, which HCD determines — not by permit
production. `fetch_compliance.py` pulls HCD's published finding for each jurisdiction and the
app reports it verbatim, with the date HCD completed its review. Under Gov. Code 65589.5(d)(5),
a jurisdiction whose element is out of substantial compliance cannot deny a qualifying project
with at least 20% lower-income units (or 100% moderate) for inconsistency with its zoning or
general plan. SB 423 streamlining additionally depends on HCD's annual production determination,
published on HCD's [SMAP dashboard](https://www.hcd.ca.gov/planning-and-community-development/streamlined-ministerial-approval-process-dashboard)
and not reproduced here.

## ADU compliance

State law (AB 68/881) requires ministerial approval of ADU applications within **60 days**.
The tracker surfaces cities whose median exceeds it with a "⚠ exceeds 60-day limit" flag.
Because the clock starts at submittal, a median above 60 days is evidence of a process running
past the statutory deadline, but not proof of a violation in any individual case — the clock
can be reset by a resubmittal, which the APR does not report.

## Known data quality issues

0. **The newest reporting year is always partial.** An APR covering calendar year N is due to
   HCD by April 1 of year N+1, and HCD publishes each one as it is filed. Some jurisdictions
   file late or not at all. `viz_data.json` carries `metadata.latest_year_not_filed` and a
   per-city `latest_reported_year`; the UI marks any city whose data stops short of the
   newest year, so a late filer is not mistaken for a city that permitted nothing.

1. **Self-reported data:** APR data is submitted by cities. HCD performs completeness checks but not field-level audits. Cities with incentives to underreport delays may do so.

2. **Date field compliance:** Many cities leave application/permit date fields blank, especially pre-2023. The tracker displays field-completion rate as its own accountability metric.

3. **Join quality:** Timeline medians are computed by joining Table A (applications) to Table A2 (permits) on jurisdiction + address + unit type. Match quality varies. Low match rates are surfaced in the drilldown.

4. **Double-counting:** The same project may appear in multiple APR years as it progresses through the pipeline. Unit count totals should be interpreted as cumulative activity, not net new units.

5. **APR URL stability:** The CSV resource URLs on data.ca.gov have changed once since 2022. `fetch_apr.py` includes a CKAN API fallback to auto-discover new URLs.

## Deployment (GitHub Pages)

1. In repo Settings → Pages, set source to `docs/` folder on `main`.
2. The `refresh_data.yml` workflow runs every Monday and commits an updated `viz_data.json`.
3. The frontend is a single static `docs/index.html` — no build step.

## License

Data: California HCD APR (CC BY), city open data portals (public domain / CC BY).
Code: MIT.

## See also

- [Peninsula enrollment vs. housing tracker](https://peninsulaforeveryone.github.io/peninsula-enrollment-decline/) — rebuts the "new housing overwhelms schools" argument
