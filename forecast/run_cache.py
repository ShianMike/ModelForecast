"""
Persistent run-based forecast cache backed by Google Cloud Storage.

This cache stores the final thinned JSON payload returned by /api/forecast,
keyed by model + run + variable + forecast hour + bbox. A small "latest"
manifest points each logical request to the most recently successful run.
"""

import gzip
import json
import logging
import os
import threading
import time

from google.cloud import storage

log = logging.getLogger(__name__)

_BUCKET_ENV = "FORECAST_CACHE_BUCKET"
_PREFIX_ENV = "FORECAST_CACHE_PREFIX"
_PREFIX_DEFAULT = "forecast-run-cache"
_SCHEMA_VERSION = "v1"
_LOCAL_TTL = 120
_PENDING_RETRY_SECONDS = 300

_client = None
_client_lock = threading.Lock()
_local_cache = {}
_local_cache_lock = threading.Lock()


def is_enabled():
    return bool(os.environ.get(_BUCKET_ENV, "").strip())


def supports_model(model):
    return model.lower() in {"gfs", "hrrr", "nam", "rap"}


def resolve_candidate_run(model):
    model_key = model.lower()
    from forecast import nomads
    from forecast import aws_grib

    if nomads.is_nomads_model(model_key):
        run_date, run_cycle = nomads._find_latest_run(model_key)
        return f"{run_date}/{run_cycle:02d}z"
    if aws_grib.is_aws_model(model_key):
        run_date, run_cycle = aws_grib._find_latest_run(model_key)
        return f"{run_date}/{run_cycle:02d}z"
    return None


def _bucket_name():
    return os.environ.get(_BUCKET_ENV, "").strip()


def _prefix():
    raw = os.environ.get(_PREFIX_ENV, _PREFIX_DEFAULT).strip().strip("/")
    return raw or _PREFIX_DEFAULT


def _get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            _client = storage.Client()
    return _client


def _get_bucket():
    name = _bucket_name()
    if not name:
        return None
    return _get_client().bucket(name)


def _local_get(key):
    with _local_cache_lock:
        entry = _local_cache.get(key)
        if entry and (time.time() - entry["ts"]) < _LOCAL_TTL:
            return entry["data"]
        if entry:
            del _local_cache[key]
    return None


def _local_set(key, data):
    with _local_cache_lock:
        if len(_local_cache) > 500:
            expired = [
                k for k, v in _local_cache.items()
                if (time.time() - v["ts"]) >= _LOCAL_TTL
            ]
            for k in expired[:200]:
                _local_cache.pop(k, None)
        _local_cache[key] = {"data": data, "ts": time.time()}


def _bbox_token(bbox):
    if not bbox:
        return "default"
    lat_min = min(float(bbox["lat_min"]), float(bbox["lat_max"]))
    lat_max = max(float(bbox["lat_min"]), float(bbox["lat_max"]))
    lon_min = min(float(bbox["lon_min"]), float(bbox["lon_max"]))
    lon_max = max(float(bbox["lon_min"]), float(bbox["lon_max"]))
    return f"{lat_min:.1f}_{lat_max:.1f}_{lon_min:.1f}_{lon_max:.1f}"


def _manifest_path(model, variable, fhour, bbox):
    return (
        f"{_prefix()}/{_SCHEMA_VERSION}/latest/"
        f"{model.lower()}/{variable}/f{int(fhour):03d}/{_bbox_token(bbox)}.json"
    )


def _payload_path(model, run, variable, fhour, bbox):
    run_token = str(run).replace("/", "-")
    return (
        f"{_prefix()}/{_SCHEMA_VERSION}/runs/"
        f"{model.lower()}/{run_token}/{variable}/f{int(fhour):03d}/{_bbox_token(bbox)}.json.gz"
    )


def _download_json(blob_path):
    cached = _local_get(blob_path)
    if cached is not None:
        return cached

    bucket = _get_bucket()
    if bucket is None:
        return None

    blob = bucket.blob(blob_path)
    if not blob.exists():
        return None

    raw = blob.download_as_bytes()
    if blob_path.endswith(".gz"):
        raw = gzip.decompress(raw)
    data = json.loads(raw.decode("utf-8"))
    _local_set(blob_path, data)
    return data


def load_latest(model, variable, fhour, bbox, desired_run=None):
    if not is_enabled() or not supports_model(model):
        return None

    try:
        manifest = _download_json(_manifest_path(model, variable, fhour, bbox))
        if not manifest:
            return None
        if desired_run and manifest.get("run") != desired_run:
            return None

        payload_path = manifest.get("payload_path")
        if not payload_path:
            payload_path = _payload_path(model, manifest.get("run"), variable, fhour, bbox)

        payload = _download_json(payload_path)
        if not payload:
            return None
        return payload
    except Exception as exc:
        log.warning("Persistent forecast cache read failed for %s/%s: %s", model, variable, exc)
        return None


def load_any_latest(model, variable, fhour, bbox):
    return load_latest(model, variable, fhour, bbox, desired_run=None)


def load_entry(model, variable, fhour, bbox):
    if not is_enabled() or not supports_model(model):
        return None

    try:
        manifest_path = _manifest_path(model, variable, fhour, bbox)
        manifest = _download_json(manifest_path)
        if not manifest:
            return None

        payload_path = manifest.get("payload_path")
        if not payload_path:
            payload_path = _payload_path(
                model,
                manifest.get("run"),
                variable,
                fhour,
                bbox,
            )

        payload = _download_json(payload_path)
        if not payload:
            return None

        return {"manifest": manifest, "payload": payload}
    except Exception as exc:
        log.warning("Persistent forecast cache entry read failed for %s/%s: %s", model, variable, exc)
        return None


def should_serve_entry(entry, candidate_run):
    if not entry or not candidate_run:
        return False

    manifest = entry["manifest"]
    requested_run = manifest.get("requested_run") or manifest.get("run")
    actual_run = manifest.get("run")
    updated_at = int(manifest.get("updated_at", 0))

    if requested_run != candidate_run:
        return False
    if actual_run == candidate_run:
        return True

    age = time.time() - updated_at
    return age < _PENDING_RETRY_SECONDS


def entry_cache_status(entry, candidate_run):
    if not entry or not candidate_run:
        return "miss"
    actual_run = entry["manifest"].get("run")
    return "hit" if actual_run == candidate_run else "stale"


def store_latest(model, variable, fhour, bbox, payload, requested_run=None):
    if not is_enabled() or not supports_model(model):
        return False

    run = payload.get("run")
    if not run:
        return False

    try:
        bucket = _get_bucket()
        if bucket is None:
            return False

        payload_path = _payload_path(model, run, variable, fhour, bbox)
        manifest_path = _manifest_path(model, variable, fhour, bbox)

        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        compressed = gzip.compress(body, compresslevel=6)

        payload_blob = bucket.blob(payload_path)
        payload_blob.upload_from_string(compressed, content_type="application/gzip")

        manifest = {
            "model": model.lower(),
            "variable": variable,
            "forecast_hour": int(fhour),
            "run": run,
            "requested_run": requested_run or run,
            "payload_path": payload_path,
            "bbox": _bbox_token(bbox),
            "updated_at": int(time.time()),
        }
        manifest_blob = bucket.blob(manifest_path)
        manifest_blob.upload_from_string(
            json.dumps(manifest, separators=(",", ":"), sort_keys=True).encode("utf-8"),
            content_type="application/json",
        )

        _local_set(payload_path, payload)
        _local_set(manifest_path, manifest)
        return True
    except Exception as exc:
        log.warning("Persistent forecast cache write failed for %s/%s: %s", model, variable, exc)
        return False
