"""Tests for the artifact inventory/status helpers and route."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import app
from forecast import artifact_cache, artifact_status


CONUS_BBOX = {"lat_min": 24.0, "lat_max": 50.0, "lon_min": -125.0, "lon_max": -66.0}


def _write_artifact(root: Path, model: str, variable: str, fhour: int, *,
                    generated_at: str, run: str = "20260523/06z",
                    bbox=None, body=None):
    bbox = bbox or CONUS_BBOX
    payload = body if body is not None else {
        "model": model,
        "variable": variable,
        "forecast_hour": fhour,
        "lats": [25.0],
        "lons": [-100.0],
        "values": [[1.0]],
        "unit": "",
        "run": run,
        "valid_time": "2026-05-23T06:00:00Z",
        "artifact_cycle": run,
        "artifact_generated_at": generated_at,
        "source": "precomputed_artifact",
    }

    target = artifact_cache.payload_path(model, variable, fhour, bbox)
    target.parent.mkdir(parents=True, exist_ok=True)
    # When FORECAST_ARTIFACT_DIR is set to a tempdir, ``payload_path`` already
    # resolves under it; keep ``root`` only as documentation of intent.
    assert str(target).startswith(str(root)), f"{target} not under {root}"
    if isinstance(payload, str):
        target.write_text(payload, encoding="utf-8")
    else:
        target.write_text(json.dumps(payload), encoding="utf-8")


class ArtifactStatusHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env_patcher = patch.dict(
            os.environ, {"FORECAST_ARTIFACT_DIR": str(self.root)}, clear=False
        )
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)
        # The artifact cache keeps a process-wide in-memory cache. Clear it so
        # earlier tests cannot leak previously-read payloads into this run.
        artifact_cache._local_cache.clear()
        self.now = datetime(2026, 5, 23, 9, 0, tzinfo=timezone.utc)

    def _write(self, variable, fhour, *, generated_at=None, body=None):
        _write_artifact(
            self.root,
            "hrrr",
            variable,
            fhour,
            generated_at=generated_at or self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            body=body,
        )

    def test_complete_inventory_marks_fresh_artifact(self):
        for fhour in range(0, 49):
            self._write("stp_approx", fhour)

        status = artifact_status.variable_status(
            "hrrr",
            "stp_approx",
            "conus",
            expected_hours=range(0, 49),
            now=self.now,
        )

        self.assertEqual(status["coverage"], "49/49")
        self.assertEqual(status["missing_hours"], [])
        self.assertEqual(status["malformed_hours"], [])
        self.assertTrue(status["complete"])
        self.assertFalse(status["stale"])
        self.assertEqual(status["latest_cycle"], "20260523/06z")
        self.assertIsNotNone(status["artifact_generated_at"])
        self.assertEqual(status["region"], "conus")

    def test_missing_hours_are_reported(self):
        for fhour in (0, 1, 2):
            self._write("scp", fhour)

        status = artifact_status.variable_status(
            "hrrr",
            "scp",
            "conus",
            expected_hours=(0, 1, 2, 3, 4),
            now=self.now,
        )

        self.assertEqual(status["available_hours"], [0, 1, 2])
        self.assertEqual(status["missing_hours"], [3, 4])
        self.assertFalse(status["complete"])
        self.assertTrue(status["stale"])

    def test_stale_threshold_flags_old_generation_timestamp(self):
        generated = (self.now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for fhour in (0, 1):
            self._write("ship", fhour, generated_at=generated)

        status = artifact_status.variable_status(
            "hrrr",
            "ship",
            "conus",
            expected_hours=(0, 1),
            stale_threshold_hours=9,
            now=self.now,
        )

        self.assertTrue(status["stale"])
        self.assertGreater(status["age_hours"], 9)

    def test_mixed_fresh_and_stale_hours_marks_variable_stale(self):
        fresh = self.now.strftime("%Y-%m-%dT%H:%M:%SZ")
        stale = (self.now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._write("scp", 0, generated_at=fresh)
        self._write("scp", 1, generated_at=stale)

        status = artifact_status.variable_status(
            "hrrr",
            "scp",
            "conus",
            expected_hours=(0, 1),
            stale_threshold_hours=9,
            now=self.now,
        )

        self.assertTrue(status["complete"])
        self.assertTrue(status["stale"])
        self.assertEqual(status["stale_hours"], [1])
        self.assertEqual(status["age_hours"], 0.0)
        self.assertGreater(status["oldest_age_hours"], 9)

    def test_malformed_json_is_surfaced_without_crashing(self):
        self._write("effective_bulk_shear", 0)  # valid
        _write_artifact(
            self.root,
            "hrrr",
            "effective_bulk_shear",
            1,
            generated_at="invalid",
            body="{not json",
        )

        status = artifact_status.variable_status(
            "hrrr",
            "effective_bulk_shear",
            "conus",
            expected_hours=(0, 1),
            now=self.now,
        )

        self.assertEqual(status["available_hours"], [0])
        self.assertEqual(len(status["malformed_hours"]), 1)
        self.assertEqual(status["malformed_hours"][0]["forecast_hour"], 1)
        self.assertIn("malformed_json", status["malformed_hours"][0]["error"])
        self.assertFalse(status["complete"])
        self.assertTrue(status["stale"])

    def test_missing_generated_at_treated_as_stale(self):
        body = {
            "model": "hrrr",
            "variable": "critical_angle_composite",
            "forecast_hour": 0,
            "values": [[1.0]],
            "lats": [25.0],
            "lons": [-100.0],
            "run": "20260523/06z",
        }
        _write_artifact(
            self.root,
            "hrrr",
            "critical_angle_composite",
            0,
            generated_at="",
            body=body,
        )

        status = artifact_status.variable_status(
            "hrrr",
            "critical_angle_composite",
            "conus",
            expected_hours=(0,),
            now=self.now,
        )

        self.assertEqual(status["available_hours"], [0])
        self.assertTrue(status["stale"])
        self.assertIsNone(status["artifact_generated_at"])

    def test_build_status_summarizes_overall_state(self):
        for variable in artifact_status.HRRR_SEVERE_COMPOSITES:
            for fhour in range(0, 49):
                self._write(variable, fhour)

        payload = artifact_status.build_status(now=self.now)

        self.assertFalse(payload["summary"]["stale"])
        self.assertTrue(payload["summary"]["complete"])
        self.assertEqual(payload["summary"]["missing_count"], 0)
        self.assertEqual(payload["summary"]["malformed_count"], 0)
        self.assertEqual(payload["stale_threshold_hours"], 9)

        hrrr_entry = next(m for m in payload["models"] if m["model"] == "hrrr")
        self.assertEqual(
            sorted(v["variable"] for v in hrrr_entry["variables"]),
            sorted(artifact_status.HRRR_SEVERE_COMPOSITES),
        )

    def test_build_status_flags_missing_artifact_set(self):
        # Only write one of the expected variables; the rest should show up
        # as completely missing.
        for fhour in range(0, 49):
            self._write("stp_approx", fhour)

        payload = artifact_status.build_status(now=self.now)

        self.assertTrue(payload["summary"]["stale"])
        self.assertFalse(payload["summary"]["complete"])
        self.assertGreater(payload["summary"]["missing_count"], 0)


class VerifyArtifactsScriptTests(unittest.TestCase):
    """Smoke-test the CLI wrapper used by the GitHub Actions workflow."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env_patcher = patch.dict(
            os.environ, {"FORECAST_ARTIFACT_DIR": str(self.root)}, clear=False
        )
        self.env_patcher.start()
        self.addCleanup(self.env_patcher.stop)
        artifact_cache._local_cache.clear()

        # Make sure the script module can be imported by absolute path during
        # tests without polluting sys.modules permanently.
        repo_root = Path(__file__).resolve().parents[1]
        scripts_root = str(repo_root / "scripts")
        if scripts_root not in sys.path:
            sys.path.insert(0, scripts_root)
            self.addCleanup(lambda: sys.path.remove(scripts_root))

    def _seed_complete_coverage(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for variable in artifact_status.HRRR_SEVERE_COMPOSITES:
            for fhour in range(0, 49):
                _write_artifact(self.root, "hrrr", variable, fhour, generated_at=now)

    def test_verify_script_exit_codes(self):
        import importlib

        verify = importlib.import_module("verify_forecast_artifacts")
        importlib.reload(verify)

        # Missing coverage -> exit 1
        buf = io.StringIO()
        with redirect_stdout(buf), patch.object(sys, "argv", ["verify_forecast_artifacts.py"]):
            rc_missing = verify.main()
        self.assertEqual(rc_missing, 1)
        self.assertIn("incomplete", buf.getvalue())

        # Complete coverage -> exit 0
        self._seed_complete_coverage()
        buf = io.StringIO()
        with redirect_stdout(buf), patch.object(sys, "argv", ["verify_forecast_artifacts.py"]):
            rc_ok = verify.main()
        self.assertEqual(rc_ok, 0)
        self.assertIn("OK: artifact coverage is complete", buf.getvalue())

        # --json should be machine-readable stdout without the human summary
        # appended after the JSON payload.
        buf = io.StringIO()
        with redirect_stdout(buf), patch.object(sys, "argv", ["verify_forecast_artifacts.py", "--json"]):
            rc_json = verify.main()
        self.assertEqual(rc_json, 0)
        parsed = json.loads(buf.getvalue())
        self.assertTrue(parsed["summary"]["complete"])
        self.assertEqual(parsed["summary"]["missing_count"], 0)

        # --require-fresh against artificially stale timestamps -> exit 2
        with patch(
            "verify_forecast_artifacts.artifact_status.build_status",
            return_value={
                "schema_version": "v1",
                "artifact_root": str(self.root),
                "artifact_root_exists": True,
                "generated_at": "2026-05-23T09:00:00Z",
                "stale_threshold_hours": 9,
                "models": [{"model": "hrrr", "variables": []}],
                "summary": {
                    "missing_count": 0,
                    "malformed_count": 0,
                    "stale": True,
                    "complete": True,
                },
            },
        ):
            buf = io.StringIO()
            with redirect_stdout(buf), patch.object(
                sys, "argv", ["verify_forecast_artifacts.py", "--require-fresh"]
            ):
                rc_stale = verify.main()
            self.assertEqual(rc_stale, 2)
            self.assertIn("stale", buf.getvalue())


class ArtifactsRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()

    def test_status_route_returns_summary_payload(self):
        sample = {
            "schema_version": "v1",
            "artifact_root": "/tmp/forecast_artifacts",
            "artifact_root_exists": True,
            "generated_at": "2026-05-23T09:00:00Z",
            "stale_threshold_hours": 9,
            "models": [
                {
                    "model": "hrrr",
                    "variables": [
                        {
                            "model": "hrrr",
                            "variable": "stp_approx",
                            "region": "conus",
                            "expected_hours": [0, 1, 2],
                            "available_hours": [0, 1, 2],
                            "missing_hours": [],
                            "malformed_hours": [],
                            "coverage": "3/3",
                            "complete": True,
                            "latest_cycle": "20260523/06z",
                            "artifact_generated_at": "2026-05-23T08:30:00Z",
                            "age_hours": 0.5,
                            "stale": False,
                            "stale_threshold_hours": 9,
                        }
                    ],
                }
            ],
            "summary": {
                "missing_count": 0,
                "malformed_count": 0,
                "stale": False,
                "complete": True,
            },
        }

        with patch("routes.artifacts.artifact_status.build_status", return_value=sample):
            response = self.client.get("/api/artifacts/status")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body, sample)


if __name__ == "__main__":
    unittest.main()
