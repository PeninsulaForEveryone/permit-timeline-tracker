"""
run_all.py — Pipeline orchestrator.

Usage:
    python -m pipeline.run_all                # fetch (cached) + transform
    python -m pipeline.run_all --force        # re-download everything
    python -m pipeline.run_all --step fetch   # fetch only
    python -m pipeline.run_all --step transform
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Peninsula Permit Tracker pipeline")
    parser.add_argument("--force", action="store_true", help="Re-download raw data even if cached")
    parser.add_argument("--step", choices=["fetch", "boundaries", "transform"], default=None,
                        help="Run only one step (default: all)")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    t0 = time.time()

    if args.step in (None, "boundaries"):
        log.info("=== Step 1b: Fetch city boundaries ===")
        from pipeline.fetch_boundaries import fetch_boundaries
        fetch_boundaries(force=args.force)

    if args.step in (None, "fetch"):
        log.info("=== Step 1: Fetch APR data ===")
        from pipeline.fetch_apr import fetch_all
        df_a, df_a2 = fetch_all(force=args.force)
        log.info("Fetch complete. Table A: %d rows, Table A2: %d rows", len(df_a), len(df_a2))

        log.info("=== Step 1c: Fetch housing element compliance ===")
        from pipeline.fetch_compliance import fetch_compliance
        compliance = fetch_compliance(force=args.force)

    if args.step in (None, "transform"):
        log.info("=== Step 2: Transform → viz_data.json ===")
        if args.step == "transform":
            # Re-load from cache
            from pipeline.fetch_apr import fetch_all
            from pipeline.fetch_compliance import fetch_compliance
            df_a, df_a2 = fetch_all(force=False)
            compliance = fetch_compliance(force=False)
        from pipeline.transform import build_viz_data, write_viz_data
        data = build_viz_data(df_a, df_a2, compliance)
        out = write_viz_data(data)
        _print_summary(data)
        log.info("Output: %s", out)

    elapsed = time.time() - t0
    log.info("Done in %.1fs", elapsed)


def _print_summary(data: dict):
    cities = data["cities"]
    print(f"\n{'Rank':<5} {'City':<24} {'Score':>6} {'RHNA %':>8} {'Conv %':>8} {'Med days':>10} {'Source'}")
    print("-" * 75)
    for c in cities:
        rhna = f"{c['rhna_progress_pct']}%" if c['rhna_progress_pct'] is not None else "—"
        conv = f"{c.get('entitlement_rate_pct', '')}%" if c.get('entitlement_rate_pct') is not None else "—"
        days = str(c['median_days_to_permit']) if c['median_days_to_permit'] is not None else "—"
        source = "APR+portal" if c['data_source'] == 'hybrid' else "HCD APR"
        print(f"{c['rank']:<5} {c['city']:<24} {c['friction_score']:>6} {rhna:>8} {conv:>8} {days:>10} {source}")
    print(f"\nTotal cities: {len(cities)}")
    missing_timeline = sum(1 for c in cities if c['median_days_to_permit'] is None)
    if missing_timeline:
        print(f"Cities with no timeline data (did not report dates to HCD): {missing_timeline}")

    meta = data["metadata"]
    yr = meta.get("latest_apr_year")
    not_filed = meta.get("latest_year_not_filed") or []
    print(f"{yr} APR filed by {meta.get('latest_year_filed_count')} of {len(cities)} jurisdictions"
          + (f" — not yet filed: {', '.join(not_filed)}" if not_filed else ""))

    out_of_compliance = [c['city'] for c in cities
                         if (c.get('housing_element') or {}).get('in_compliance') is False]
    print("Housing element OUT of compliance (Builder's Remedy applies): "
          + (", ".join(out_of_compliance) if out_of_compliance else "none"))


if __name__ == "__main__":
    main()
