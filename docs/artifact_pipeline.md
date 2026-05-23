# Forecast Artifact Pipeline

Production currently serves the five HRRR CONUS severe composites from
precomputed JSON artifacts so the free Render dyno never has to compute
multi-field derived products inside a user request. This document captures
the moving parts so future deploys are predictable and missing artifacts
are noticed before they reach users.

## What lives where

| Item | Path | Notes |
| --- | --- | --- |
| Generator | `scripts/generate_forecast_artifacts.py` | Fetches components, computes composites, writes JSON. |
| Verifier | `scripts/verify_forecast_artifacts.py` | Wraps `forecast.artifact_status` for CI + smoke tests. |
| Filesystem cache | `forecast_artifacts/v1/latest/<model>/<variable>/f###/<bbox>.json` | Read by `forecast.artifact_cache.load_latest`. |
| Status API | `routes/artifacts.py` -> `/api/artifacts/status` | Read-only inventory + freshness view. |
| Inventory logic | `forecast/artifact_status.py` | Shared by API, verifier, tests. |

## Generation schedule

GitHub Actions runs `.github/workflows/forecast-artifacts.yml` on a schedule
of `30 2,8,14,20 * * *` UTC, i.e. 30 minutes past 02z / 08z / 14z / 20z.
That hits each synoptic cycle two hours after the run start so HRRR data is
reliably available from NOMADS / AWS by the time the job kicks off.

The job currently produces:

- Models: `hrrr`
- Region: `conus` (`lat_min=24, lat_max=50, lon_min=-125, lon_max=-66`)
- Variables: `effective_bulk_shear`, `stp_approx`, `scp`, `ship`,
  `critical_angle_composite`
- Forecast hours: `f000`..`f048` (49 files per variable)

After generation, the workflow runs the verifier and only commits if every
expected file is present and parseable. Missing or malformed artifacts fail
the job loudly so a bad cycle does not silently overwrite a good one in the
repo.

## Stale threshold

`forecast.artifact_status.DEFAULT_STALE_HOURS = 9`. Artifacts are scheduled
every 6 hours with a 2-hour availability buffer, so anything older than
~9 hours typically means a missed refresh. The `/api/artifacts/status`
response reports `stale_threshold_hours` and `age_hours` so dashboards can
adjust without code changes.

## Render deploy behavior

Render is configured (in `render.yaml`) to auto-deploy on every push to the
default branch. Because the GitHub Actions artifact job commits refreshed
JSON files directly into the repo (`chore: refresh forecast artifacts`),
each successful refresh triggers a Render redeploy. Render's free dyno
serves the new artifacts within a few minutes of the commit.

The deployed app sets `FORECAST_ARTIFACT_ONLY_VARIABLES` to the five severe
composites, so requests for them either return the artifact or a structured
503 (`code: "artifact_missing"`). Live composite computation is intentionally
not used as a fallback on Render — the free dyno does not have the headroom
to run a multi-field GRIB join inside a request.

## Production smoke tests

After a Render deploy completes, run these checks against the live URL
(`https://modelforecastpy.app`) before declaring success. Adjust the host as
needed for `*.onrender.com` previews.

```bash
BASE=https://modelforecastpy.app

curl -sf "$BASE/api/health"
curl -sf "$BASE/api/artifacts/status" | python -m json.tool | head -40

for VAR in stp_approx scp ship effective_bulk_shear critical_angle_composite; do
  for FHOUR in 0 6 24 48; do
    curl -sf -o /dev/null -w "%{http_code} $VAR f$FHOUR\n" \
      "$BASE/api/forecast?model=hrrr&variable=$VAR&fhour=$FHOUR&lat_min=24&lat_max=50&lon_min=-125&lon_max=-66"
  done
done
```

Expectations:

- `/api/health` returns `{"status":"ok"}`.
- `/api/artifacts/status` reports `summary.complete=true` and
  `summary.stale=false` after a fresh artifact cycle finishes deploying.
- Each composite forecast probe returns `200`. A `503` with
  `code: "artifact_missing"` indicates the artifact for that hour was not
  written; check the latest `Generate Forecast Artifacts` workflow run.

For a local end-to-end smoke run on the current artifacts:

```bash
python scripts/verify_forecast_artifacts.py
python scripts/verify_forecast_artifacts.py --require-fresh
```

The first call exits non-zero on missing or malformed coverage. The second
call additionally fails if any artifact is older than the stale threshold
(useful right after a refresh cycle but expected to fail in between cycles).
