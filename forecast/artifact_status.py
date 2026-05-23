"""
Read-only inventory and freshness helpers for forecast artifacts on disk.

The artifact cache writes JSON payloads under `forecast_artifacts/v1/latest/...`.
This module derives a structured status view used by `/api/artifacts/status`
so missing or stale artifact coverage is obvious without parsing each file.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Iterable

from forecast import artifact_cache

log = logging.getLogger(__name__)

# Default stale threshold = 9h: artifacts are scheduled every 6h with a
# 2h availability buffer, so anything older than 9h likely means a missed
# refresh cycle and should be flagged in the dashboard / smoke tests.
DEFAULT_STALE_HOURS = 9

# Named region presets exposed by the artifact generator.
REGIONS = {
    "conus": {"lat_min": 24.0, "lat_max": 50.0, "lon_min": -125.0, "lon_max": -66.0},
}

# Default coverage spec for the stabilized HRRR CONUS severe composites.
HRRR_SEVERE_COMPOSITES = (
    "effective_bulk_shear",
    "stp_approx",
    "scp",
    "ship",
    "critical_angle_composite",
)

DEFAULT_COVERAGE = {
    "hrrr": {
        "variables": HRRR_SEVERE_COMPOSITES,
        "regions": ("conus",),
        "hours": tuple(range(0, 49)),
    },
}


def _bbox_for_region(region: str):
    bbox = REGIONS.get(region)
    if bbox is None:
        raise ValueError(f"Unknown artifact region: {region}")
    return bbox


def _parse_iso8601(value: str):
    """Parse an ISO 8601 UTC timestamp, accepting the trailing 'Z' suffix."""
    if not value or not isinstance(value, str):
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_payload_metadata(path):
    """Read minimal metadata fields from a stored artifact JSON file.

    Returns ``None`` if the file is missing. Returns a dict with an ``error``
    key when the file exists but cannot be parsed, so callers can surface
    malformed artifacts without crashing.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("Artifact read failed for %s: %s", path, exc)
        return {"error": f"read_failed: {exc}"}

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        log.warning("Artifact JSON malformed for %s: %s", path, exc)
        return {"error": f"malformed_json: {exc.msg}"}

    if not isinstance(data, dict):
        return {"error": "malformed_json: payload is not an object"}

    return {
        "run": data.get("run"),
        "artifact_cycle": data.get("artifact_cycle") or data.get("run"),
        "artifact_generated_at": data.get("artifact_generated_at"),
        "source": data.get("source"),
    }


def _age_hours(generated_at: str, *, now: datetime | None = None):
    parsed = _parse_iso8601(generated_at)
    if parsed is None:
        return None
    current = now or datetime.now(timezone.utc)
    delta_seconds = (current - parsed).total_seconds()
    return round(delta_seconds / 3600.0, 2)


def is_stale(generated_at: str, *, threshold_hours: float = DEFAULT_STALE_HOURS, now=None):
    """Return True when ``generated_at`` is older than ``threshold_hours``.

    Missing or unparseable timestamps are treated as stale because we cannot
    confirm the artifact came from a recent refresh cycle.
    """
    age = _age_hours(generated_at, now=now)
    if age is None:
        return True
    return age >= threshold_hours


def variable_status(
    model: str,
    variable: str,
    region: str,
    *,
    expected_hours: Iterable[int],
    stale_threshold_hours: float = DEFAULT_STALE_HOURS,
    now: datetime | None = None,
):
    """Build a structured status entry for a single (model, variable, region).

    The shape is stable so the status endpoint, smoke tests and frontend can
    all rely on the same fields.
    """
    bbox = _bbox_for_region(region)
    expected = sorted({int(h) for h in expected_hours})

    available: list[int] = []
    missing: list[int] = []
    malformed: list[dict] = []
    stale_hours: list[int] = []
    latest_metadata = None
    latest_age_hours = None
    oldest_age_hours = None

    for fhour in expected:
        path = artifact_cache.payload_path(model, variable, fhour, bbox)
        meta = _read_payload_metadata(path)
        if meta is None:
            missing.append(fhour)
            continue

        if "error" in meta:
            malformed.append({"forecast_hour": fhour, "error": meta["error"]})
            continue

        available.append(fhour)

        generated_at = meta.get("artifact_generated_at")
        candidate_age = _age_hours(generated_at, now=now)
        if candidate_age is None:
            stale_hours.append(fhour)
        else:
            if oldest_age_hours is None or candidate_age > oldest_age_hours:
                oldest_age_hours = candidate_age
            if candidate_age >= stale_threshold_hours:
                stale_hours.append(fhour)

        if latest_metadata is None or (
            candidate_age is not None
            and (latest_age_hours is None or candidate_age < latest_age_hours)
        ):
            latest_metadata = meta
            latest_age_hours = candidate_age

    generated_at = latest_metadata.get("artifact_generated_at") if latest_metadata else None
    cycle = latest_metadata.get("artifact_cycle") if latest_metadata else None

    if missing or malformed or latest_metadata is None:
        stale_flag = True
    else:
        stale_flag = bool(stale_hours)

    return {
        "model": model.lower(),
        "variable": variable,
        "region": region,
        "expected_hours": expected,
        "available_hours": available,
        "missing_hours": missing,
        "malformed_hours": malformed,
        "coverage": f"{len(available)}/{len(expected)}",
        "complete": not missing and not malformed,
        "latest_cycle": cycle,
        "artifact_generated_at": generated_at,
        "age_hours": latest_age_hours,
        "oldest_age_hours": oldest_age_hours,
        "stale_hours": stale_hours,
        "stale": stale_flag,
        "stale_threshold_hours": stale_threshold_hours,
    }


def build_status(
    coverage: dict | None = None,
    *,
    stale_threshold_hours: float = DEFAULT_STALE_HOURS,
    now: datetime | None = None,
):
    """Build a full status payload for the configured coverage map.

    The default coverage focuses on HRRR CONUS severe composites which is the
    first artifact set stabilized for production. Callers can pass a custom
    coverage map shaped like :data:`DEFAULT_COVERAGE` for additional models.
    """
    coverage = coverage if coverage is not None else DEFAULT_COVERAGE
    artifact_root_path = artifact_cache._artifact_root()

    models_payload: list[dict] = []
    overall_missing = 0
    overall_malformed = 0
    any_stale = False
    any_complete = True

    for model, spec in coverage.items():
        variables_payload: list[dict] = []
        regions = spec.get("regions", ("conus",))
        hours = spec.get("hours", tuple())
        for region in regions:
            for variable in spec.get("variables", ()):
                status = variable_status(
                    model,
                    variable,
                    region,
                    expected_hours=hours,
                    stale_threshold_hours=stale_threshold_hours,
                    now=now,
                )
                variables_payload.append(status)
                overall_missing += len(status["missing_hours"])
                overall_malformed += len(status["malformed_hours"])
                if status["stale"]:
                    any_stale = True
                if not status["complete"]:
                    any_complete = False
        models_payload.append({"model": model.lower(), "variables": variables_payload})

    return {
        "schema_version": artifact_cache._SCHEMA_VERSION,
        "artifact_root": str(artifact_root_path),
        "artifact_root_exists": artifact_root_path.exists(),
        "generated_at": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stale_threshold_hours": stale_threshold_hours,
        "models": models_payload,
        "summary": {
            "missing_count": overall_missing,
            "malformed_count": overall_malformed,
            "stale": any_stale,
            "complete": any_complete,
        },
    }
