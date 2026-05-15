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
| Friction score (0–100) | Weighted composite: RHNA gap + conversion rate + timeline |
| RHNA progress | % of 6th-cycle (2023–2031) target reached via issued permits |
| App → permit conversion rate | % of applications that reach a building permit |
| Median days to permit | Application-deemed-complete → building permit issued |
| ADU compliance | % of ADU permits issued within 60-day statutory deadline (AB 68/881) |

Cities that don't report required date fields to HCD are flagged — missing data is itself a compliance failure under Government Code 65400.

## Data sources

| Source | What | Years | URL |
|---|---|---|---|
| HCD Housing Element APR Table A | Applications deemed complete | 2018–present | [data.ca.gov](https://data.ca.gov/dataset/housing-element-annual-progress-report-apr-data-by-jurisdiction-and-year) |
| HCD Housing Element APR Table A2 | Entitlements, permits, COs | 2018–present | same |
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
  fetch_apr.py    Download & cache HCD APR Table A and A2; normalize columns
  transform.py    Compute per-city metrics, ADU sub-metrics, friction score
  run_all.py      Orchestrator: --force to re-download, --step for single steps
```

Raw files cached in `data/raw/` (gitignored). Final JSON (`docs/data/viz_data.json`) is committed and served by GitHub Pages.

## Friction score methodology

```
score = RHNA gap (40%) + conversion gap (35%) + timeline score (25%)

RHNA gap        = 1 − (permits_6th_cycle / rhna_target)
Conversion gap  = 1 − (permits / applications)
Timeline score  = median_days_to_permit / 800-day ceiling
```

When a city doesn't report date fields, timeline weight is redistributed to the other two components. The full methodology string is embedded in `viz_data.json` under `metadata.methodology`.

## ADU compliance

State law (AB 68/881) requires ministerial approval of ADU applications within **60 days**. Any city exceeding this median is in documented violation. The tracker surfaces this as a separate metric with a "⚠ exceeds 60-day limit" flag.

## Known data quality issues

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
