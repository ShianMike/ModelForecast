# Model Forecast Artifact Composites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve severe composite forecast maps from precomputed artifacts so the free Render service does not calculate multi-field products during user requests.

**Architecture:** GitHub Actions generates JSON payloads for the 00z, 06z, 12z, and 18z cycles and commits them under `forecast_artifacts/`. The Flask `/api/forecast` route reads those files before any live fetch, and Render is configured to require artifacts for the heavy severe composite variables.

**Tech Stack:** Python 3.12, Flask, existing Model Forecast route helpers, GitHub Actions, Render Docker free web service.

---

### Task 1: Filesystem Artifact Cache

**Files:**
- Create: `forecast/artifact_cache.py`
- Modify: `routes/forecast_routes.py`
- Test: `tests/test_api_routes.py`

- [x] **Step 1: Add artifact cache module**

Create `forecast/artifact_cache.py` with `load_latest(model, variable, fhour, bbox)` and `store_latest(model, variable, fhour, bbox, payload)`. Store JSON files at `forecast_artifacts/v1/latest/{model}/{variable}/f{hour}/{bbox}.json`.

- [x] **Step 2: Read artifacts before live fetches**

Modify `_load_persistent_forecast_cache()` in `routes/forecast_routes.py` so it returns an artifact payload before checking the existing Google Cloud Storage cache.

- [x] **Step 3: Add artifact-only guard**

Read `FORECAST_ARTIFACT_ONLY_VARIABLES` as a comma-separated env var. If a listed composite variable has no artifact, return HTTP `503` JSON instead of calculating it live.

- [ ] **Step 4: Verify route behavior**

Run: `.\\.venv\\Scripts\\python.exe -m pytest tests\\test_api_routes.py -q`
Expected: all API route tests pass.

### Task 2: Scheduled Artifact Generation

**Files:**
- Create: `scripts/generate_forecast_artifacts.py`
- Create: `.github/workflows/forecast-artifacts.yml`
- Modify: `render.yaml`

- [x] **Step 1: Add generator script**

Create a script that generates `effective_bulk_shear`, `stp_approx`, `scp`, `ship`, and `critical_angle_composite` artifacts for HRRR CONUS forecast hours `0` through `48`.

- [x] **Step 2: Add four-cycle schedule**

Create `.github/workflows/forecast-artifacts.yml` with cron `30 2,8,14,20 * * *`, which runs after the 00z, 06z, 12z, and 18z cycles have a two-hour availability buffer.

- [x] **Step 3: Make Render artifact-only for severe composites**

Set `FORECAST_ARTIFACT_ONLY_VARIABLES=effective_bulk_shear,stp_approx,scp,ship,critical_angle_composite` in `render.yaml`.

- [ ] **Step 4: Verify generator**

Run: `.\\.venv\\Scripts\\python.exe scripts\\generate_forecast_artifacts.py --models hrrr --regions conus --variables stp_approx --hours 0`
Expected: one JSON artifact is written under `forecast_artifacts/v1/latest/hrrr/stp_approx/f000/`.

### Task 3: Ship And Verify

**Files:**
- Modify only files from Tasks 1 and 2.

- [ ] **Step 1: Run full verification**

Run: `.\\.venv\\Scripts\\python.exe -m pytest -q`
Run: `npm --prefix frontend run build`
Expected: backend tests pass, frontend build succeeds with only existing bundle-size warnings.

- [ ] **Step 2: Commit and push**

Run: `git add .github/workflows/forecast-artifacts.yml docs/superpowers/plans/2026-05-23-model-forecast-artifact-composites.md forecast/artifact_cache.py forecast_artifacts/.gitkeep render.yaml routes/forecast_routes.py scripts/generate_forecast_artifacts.py tests/test_api_routes.py`
Run: `git commit -m "feat: serve severe composites from artifacts"`
Run: `git push origin main`

- [ ] **Step 3: Generate initial artifacts**

Run the `Generate Forecast Artifacts` workflow manually in GitHub Actions, then let Render auto-deploy the artifact commit.

- [ ] **Step 4: Live verification**

Verify `https://modelforecastpy.app/api/health` returns `200`, then verify HRRR CONUS `/api/forecast` for `stp_approx`, `scp`, `ship`, and `critical_angle_composite` returns `200` JSON without Render memory failure events.
