# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
make install      # pip install -r requirements.txt
make run          # fetch (cached) + transform → docs/data/viz_data.json
make run-force    # re-download all raw CSVs, then transform
make fetch        # fetch only (no transform)
make transform    # transform only (reads cached CSVs)
make clean        # remove data/raw/ and processed files
make lint         # py_compile check across all pipeline modules
```

Run a single pipeline step directly:
```bash
python -m pipeline.run_all --step fetch        # or: boundaries, transform
python -m pipeline.run_all --force --log-level DEBUG
```

Run a module standalone (useful for iteration):
```bash
python -m pipeline.fetch_apr [--force]
python -m pipeline.fetch_compliance [--force]
python -m pipeline.fetch_boundaries [--force]
python -m pipeline.transform
```

Re-render the social preview card after friction scores change (needs Pillow):
```bash
python tools/make_og_card.py            # writes docs/img.png at 1200×630
```

## Architecture

This is a **Python data pipeline + static frontend** project with no server.

### Data flow

```
HCD APR CSVs (data.ca.gov)          Census shapefile (Census Bureau FTP)
        ↓                                        ↓
pipeline/fetch_apr.py               pipeline/fetch_boundaries.py
        ↓                                        ↓
data/raw/*.csv (gitignored)         docs/data/boundaries.geojson (committed)
        ↓
        ↓   ← pipeline/fetch_compliance.py (HCD housing element compliance CSV)
        ↓
pipeline/transform.py
        ↓
docs/data/viz_data.json (committed)
        ↓
docs/index.html (static, reads JSON via fetch)
```

### Pipeline modules

- **`pipeline/config.py`** — single source of truth: data URLs, RHNA targets, friction score weights, jurisdiction list, column aliases. Change methodology constants here.
- **`pipeline/fetch_apr.py`** — downloads Table A (applications) and Table A2 (permits/entitlements) from data.ca.gov; normalizes heterogeneous column names via `COLUMN_ALIASES`; filters to Peninsula jurisdictions; deduplicates resubmissions.
- **`pipeline/fetch_compliance.py`** — downloads HCD's Housing Element Compliance Report (a different data.ca.gov dataset from the APR) and returns per-jurisdiction compliance status. This is the only source for Builder's Remedy / SB 423 exposure; never infer it from permit production.
- **`pipeline/fetch_boundaries.py`** — downloads Census Bureau shapefile ZIP, parses `.shp` and `.dbf` files using stdlib only (no geopandas), writes `boundaries.geojson`.
- **`pipeline/transform.py`** — joins Table A → Table A2 on `(jurisdiction, address, unit_type)` to compute per-city timelines; computes friction scores, RHNA progress, ADU compliance; writes `viz_data.json`.
- **`pipeline/run_all.py`** — orchestrator with `--force` and `--step` flags.

### Key design decisions

**Column name normalization**: HCD has changed APR column names across years. `config.COLUMN_ALIASES` maps all known source variants to canonical names. `fetch_apr._rename_columns()` applies this on load, and ensures all canonical columns exist (filling missing with `pd.NA`).

**Cross-year deduplication**: Projects reappear in APR data each year they are active. `transform._dedup_cross_year()` deduplicates on `(jurisdiction, address, unit_type)`, keeping the row with the most date fields populated.

**Timeline join**: `transform._timeline_metrics()` inner-joins Table A to Table A2 on `(jurisdiction, address, unit_type)`. Match quality varies by city — `timeline_match_rate_pct` is surfaced in `viz_data.json` alongside medians.

**What the clock measures**: the canonical field is `date_application_submitted` (HCD `APP_SUBMIT_DT`), NOT a deemed-complete date. The APR schema has no deemed-complete, SB 330 pre-application, or first-contact field, so pre-submittal delay is invisible in every metric here. Do not rename this field back to `date_application_complete` or alias a deemed-complete column into it — the UI copy and methodology text both depend on the clock starting at submittal.

**Reporting coverage**: no year filter is applied. APRs for year N are due April 1 of year N+1 and HCD publishes them as filed, so the newest year is always partial. `metadata.latest_year_not_filed` and each city's `latest_reported_year` / `filed_latest_apr` carry this; the UI marks late filers instead of showing them as low producers.

**CKAN fallback**: If the direct CSV URL 404s (data.ca.gov has changed these before), `fetch_apr._download_via_ckan()` auto-discovers the current URL via the CKAN API using the stable dataset ID in `config.HCD_APR_CKAN_DATASET_ID`.

**Friction score**: Computed in `transform._friction_score()` from `config.FRICTION_WEIGHTS` (RHNA gap 60%, timeline 40%). `transform._methodology_note()` renders the published description from the same dict, so the page and the code cannot drift apart — change the weights in one place only. When a city has no timeline data, weight goes entirely to RHNA gap. Score is 0–100, higher = more friction, and it is editorial: it has no legal meaning.

**RHNA progress counts building permits**, not entitlements (HCD's rule — `NO_BUILDING_PERMITS` in Table A2). The separate `entitlement_rate_pct` comes from Table A and is not a later stage of the same funnel; Table A covers discretionary applications while Table A2 counts all permits including ministerial ADUs.

**Housing element compliance**: `transform._housing_element()` passes HCD's finding through untouched and derives only `builders_remedy_applies` (= not in compliance). Do not reintroduce risk tiers computed from the friction score — the previous version did that and it was wrong.

**ADU compliance**: Tracked separately in `transform._adu_metrics()`. State law (AB 68/881) requires ministerial approval within 60 days; this is surfaced as a flag for any city exceeding the median.

### Frontend

`docs/index.html` is a single self-contained file (no build step). It fetches `viz_data.json` and `boundaries.geojson` at runtime. GitHub Pages serves `docs/` directly. To preview locally, run `python -m http.server` from `docs/` (opening the file directly blocks the `fetch()` calls).

Basemap tiles come from Esri's World Light Gray Canvas, which needs no API key. CARTO's basemaps now stamp "API KEY REQUIRED" across unkeyed tiles — don't switch back.

`docs/img.png` is the Open Graph card, rendered by `tools/make_og_card.py` at exactly 1200×630. Social clients crop og:image to ~1.91:1, so any other aspect ratio gets sliced.

### Deployment

`docs/data/viz_data.json` and `docs/data/boundaries.geojson` are **committed to the repo** and served by GitHub Pages. The `.github/workflows/refresh_data.yml` workflow runs every Monday, re-downloads raw data with `--force`, and commits the updated JSON.

`data/raw/` is gitignored (cache only). `data/processed/` is also gitignored.
