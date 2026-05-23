"""Verify forecast artifact completeness against the tracked coverage map.

Runs after ``scripts/generate_forecast_artifacts.py`` in CI so missing or
malformed artifacts fail the workflow loudly instead of getting committed
silently. Also usable locally for sanity checks.

Examples
--------

Verify the default HRRR CONUS severe-composite coverage is complete::

    python scripts/verify_forecast_artifacts.py

Verify completeness and freshness (e.g., for production smoke tests)::

    python scripts/verify_forecast_artifacts.py --require-fresh
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forecast import artifact_status


def _format_variable_line(entry: dict) -> str:
    bullet = "OK " if entry["complete"] else "FAIL"
    if not entry["stale"]:
        freshness = "fresh"
    elif not entry["complete"]:
        freshness = "stale (incomplete)"
    else:
        age = entry.get("oldest_age_hours", entry.get("age_hours"))
        freshness = f"stale ({age}h)" if age is not None else "stale (no timestamp)"
    line = (
        f"  {bullet}  {entry['model']:<5} {entry['region']:<5} "
        f"{entry['variable']:<26} coverage={entry['coverage']:<8} "
        f"cycle={entry.get('latest_cycle') or '-':<14} freshness={freshness}"
    )
    if entry["missing_hours"]:
        missing = ",".join(f"f{h:03d}" for h in entry["missing_hours"])
        line += f"\n        missing: {missing}"
    if entry["malformed_hours"]:
        bad = ", ".join(
            f"f{m['forecast_hour']:03d} ({m['error']})"
            for m in entry["malformed_hours"]
        )
        line += f"\n        malformed: {bad}"
    return line


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify forecast artifact completeness and freshness.",
    )
    parser.add_argument(
        "--require-fresh",
        action="store_true",
        help="Fail if any tracked artifact is older than --stale-hours.",
    )
    parser.add_argument(
        "--stale-hours",
        type=float,
        default=artifact_status.DEFAULT_STALE_HOURS,
        help=(
            "Stale threshold in hours (default: "
            f"{artifact_status.DEFAULT_STALE_HOURS})."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full status payload as JSON instead of the summary.",
    )
    args = parser.parse_args()

    payload = artifact_status.build_status(stale_threshold_hours=args.stale_hours)
    summary = payload["summary"]

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        if not summary["complete"]:
            return 1
        if args.require_fresh and summary["stale"]:
            return 2
        return 0

    print(f"Artifact root: {payload['artifact_root']}")
    for model in payload["models"]:
        print(f"\nModel: {model['model']}")
        for entry in model["variables"]:
            print(_format_variable_line(entry))

    print(
        f"\nSummary: complete={summary['complete']} "
        f"missing_count={summary['missing_count']} "
        f"malformed_count={summary['malformed_count']} "
        f"stale={summary['stale']} "
        f"stale_threshold_hours={payload['stale_threshold_hours']}"
    )

    if not summary["complete"]:
        print("ERROR: artifact coverage is incomplete (missing or malformed files).")
        return 1
    if args.require_fresh and summary["stale"]:
        print("ERROR: artifact coverage is stale.")
        return 2
    print("OK: artifact coverage is complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
