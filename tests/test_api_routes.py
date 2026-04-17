import unittest
import threading
from collections import OrderedDict
from unittest.mock import patch

from app import app
from routes import forecast_routes


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def build_test_sounding(*_args, **_kwargs):
    profile = []
    for pressure, value in (
        (1000, 12.0),
        (925, 18.0),
        (850, 24.0),
        (700, 30.0),
        (500, 36.0),
        (300, 42.0),
        (250, 48.0),
        (200, 54.0),
    ):
        profile.append(
            {
                "pressure": pressure,
                "temperature": value,
                "dewpoint": max(value - 4.0, -80.0),
                "wind_speed": value,
                "wind_direction": 225.0,
                "height": 1000.0 + (1000.0 - pressure) * 8.0,
            }
        )
    return {
        "model": "gfs",
        "source_model": "gfs",
        "forecast_hour": 3,
        "valid_time": "2026-04-14T03:00:00Z",
        "profile": profile,
        "analysis": {},
        "source": "nomads_grib",
    }


class ApiRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True)

    def setUp(self):
        self.client = app.test_client()

    def test_forecast_rejects_non_numeric_bbox(self):
        response = self.client.get(
            "/api/forecast",
            query_string={
                "lat_min": "oops",
                "lat_max": "45",
                "lon_min": "-100",
                "lon_max": "-90",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": "Query parameter 'lat_min' must be a valid number."},
        )

    def test_forecast_rejects_negative_fhour_before_upstream_fetch(self):
        with patch(
            "routes.forecast_routes.nomads.fetch_grid_forecast",
            side_effect=AssertionError("upstream fetch should not run"),
        ):
            response = self.client.get(
                "/api/forecast",
                query_string={
                    "model": "gfs",
                    "variable": "temperature_2m",
                    "fhour": "-1",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": "Query parameter 'fhour' must be at least 0."},
        )

    def test_forecast_does_not_fallback_to_open_meteo_when_grib_sources_fail(self):
        with patch("routes.forecast_routes.run_cache.is_enabled", return_value=False), patch(
            "routes.forecast_routes.nomads.fetch_grid_forecast",
            side_effect=RuntimeError("nomads unavailable"),
        ), patch(
            "routes.forecast_routes.aws_grib.fetch_grid_forecast",
            side_effect=RuntimeError("aws unavailable"),
        ), patch(
            "routes.forecast_routes.open_meteo.fetch_grid_forecast",
            side_effect=AssertionError("open-meteo fallback should not run"),
        ):
            response = self.client.get(
                "/api/forecast",
                query_string={
                    "model": "gfs",
                    "variable": "temperature_2m",
                    "fhour": "0",
                },
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.get_json(),
            {"error": "Failed to fetch forecast data from upstream providers."},
        )

    def test_forecast_hrrr_does_not_fallback_to_open_meteo_when_grib_sources_fail(self):
        with patch("routes.forecast_routes.run_cache.is_enabled", return_value=False), patch(
            "routes.forecast_routes.nomads.fetch_grid_forecast",
            side_effect=FileNotFoundError("nomads file missing"),
        ), patch(
            "routes.forecast_routes.aws_grib.fetch_grid_forecast",
            side_effect=FileNotFoundError("aws file missing"),
        ), patch(
            "routes.forecast_routes.open_meteo.fetch_grid_forecast",
            side_effect=AssertionError("open-meteo fallback should not run"),
        ) as om_fetch:
            response = self.client.get(
                "/api/forecast",
                query_string={
                    "model": "hrrr",
                    "variable": "temperature_2m",
                    "fhour": "17",
                },
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.get_json(),
            {"error": "Failed to fetch forecast data from upstream providers."},
        )
        om_fetch.assert_not_called()

    def test_forecast_serves_persistent_cache_when_entry_matches_run(self):
        cached_payload = {
            "model": "gfs",
            "variable": "temperature_2m",
            "forecast_hour": 0,
            "lats": [35.0],
            "lons": [-97.0],
            "values": [[72.0]],
            "run": "20260414/00z",
        }
        entry = {"manifest": {"run": "20260414/00z"}, "payload": cached_payload}

        with patch("routes.forecast_routes.run_cache.is_enabled", return_value=True), patch(
            "routes.forecast_routes.run_cache.supports_model",
            return_value=True,
        ), patch(
            "routes.forecast_routes.run_cache.resolve_candidate_run",
            return_value="20260414/00z",
        ), patch(
            "routes.forecast_routes.run_cache.load_entry",
            return_value=entry,
        ), patch(
            "routes.forecast_routes.run_cache.should_serve_entry",
            return_value=True,
        ), patch(
            "routes.forecast_routes.nomads.fetch_grid_forecast",
            side_effect=AssertionError("upstream fetch should not run"),
        ):
            response = self.client.get(
                "/api/forecast",
                query_string={
                    "model": "gfs",
                    "variable": "temperature_2m",
                    "fhour": "0",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), cached_payload)

    def test_cross_section_rejects_out_of_range_coordinates(self):
        response = self.client.get(
            "/api/cross-section",
            query_string={
                "model": "gfs",
                "variable": "temperature",
                "fhour": "0",
                "lat1": "95",
                "lon1": "-100",
                "lat2": "40",
                "lon2": "-90",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": "Query parameter 'lat1' must be at most 90.0."},
        )

    def test_meteogram_does_not_fallback_to_open_meteo(self):
        with patch(
            "routes.forecast_routes._build_grib_meteogram",
            side_effect=RuntimeError("grib unavailable"),
        ), patch(
            "routes.forecast_routes.open_meteo.fetch_point_forecast",
            side_effect=AssertionError("open-meteo fallback should not run"),
        ):
            response = self.client.get(
                "/api/meteogram",
                query_string={
                    "model": "gfs",
                    "lat": "35",
                    "lon": "-97",
                },
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.get_json(),
            {"error": "Failed to fetch meteogram data from upstream providers."},
        )

    def test_sounding_does_not_fallback_to_open_meteo(self):
        with patch(
            "routes.forecast_routes._build_grib_sounding",
            side_effect=RuntimeError("grib unavailable"),
        ), patch(
            "routes.forecast_routes.open_meteo._request_with_retry",
            side_effect=AssertionError("open-meteo fallback should not run"),
        ):
            response = self.client.get(
                "/api/sounding",
                query_string={
                    "model": "gfs",
                    "lat": "35",
                    "lon": "-97",
                    "fhour": "3",
                },
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.get_json(),
            {"error": "Failed to fetch sounding data from upstream providers."},
        )

    def test_cross_section_uses_requested_variable(self):
        with patch(
            "routes.forecast_routes._build_grib_sounding",
            side_effect=build_test_sounding,
        ) as mock_sounding, patch(
            "routes.forecast_routes.open_meteo._request_with_retry",
            side_effect=AssertionError("open-meteo cross-section should not run"),
        ):
            response = self.client.get(
                "/api/cross-section",
                query_string={
                    "model": "gfs",
                    "variable": "wind_speed_500hPa",
                    "fhour": "3",
                    "lat1": "35",
                    "lon1": "-100",
                    "lat2": "36",
                    "lon2": "-99",
                },
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["requested_variable"], "wind_speed_500hPa")
        self.assertEqual(payload["variable"], "wind_speed")
        self.assertEqual(payload["label"], "Wind Speed")
        self.assertEqual(payload["unit"], "kt")
        self.assertEqual(payload["requested_forecast_hour"], 3)
        self.assertEqual(payload["forecast_hour"], 3)
        self.assertEqual(payload["values"][0][4], 36)
        self.assertEqual(mock_sounding.call_count, 20)

    def test_ensemble_returns_all_members(self):
        hourly = {"time": ["2026-04-14T00:00"]}
        for member_idx in range(7):
            hourly[f"temperature_2m_member{member_idx:02d}"] = [float(member_idx)]

        with patch(
            "routes.forecast_routes.open_meteo._request_with_retry",
            return_value=FakeResponse({"hourly": hourly}),
        ):
            response = self.client.get(
                "/api/ensemble",
                query_string={
                    "variable": "temperature_2m",
                    "lat": "35",
                    "lon": "-97",
                },
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["n_members"], 7)
        self.assertEqual(len(payload["members"]), 7)

    def test_ensemble_falls_back_when_requested_variable_is_unsupported(self):
        hourly = {"time": ["2026-04-14T00:00"]}
        for member_idx in range(3):
            hourly[f"temperature_2m_member{member_idx:02d}"] = [float(member_idx)]

        with patch(
            "routes.forecast_routes.open_meteo._request_with_retry",
            side_effect=[
                FakeResponse({"error": "unsupported variable"}, status_code=400),
                FakeResponse({"hourly": hourly}),
            ],
        ):
            response = self.client.get(
                "/api/ensemble",
                query_string={
                    "variable": "simulated_reflectivity",
                    "lat": "35",
                    "lon": "-97",
                },
            )

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["variable"], "simulated_reflectivity")
        self.assertEqual(payload["source_variable"], "temperature_2m")
        self.assertEqual(payload["n_members"], 3)

    def test_ttl_lru_cache_set_evicts_least_recently_used_entry(self):
        cache = OrderedDict()
        lock = threading.Lock()

        forecast_routes._ttl_lru_cache_set(cache, lock, "a", {"value": 1}, max_size=2)
        forecast_routes._ttl_lru_cache_set(cache, lock, "b", {"value": 2}, max_size=2)
        forecast_routes._ttl_lru_cache_get(cache, lock, "a", ttl_seconds=900)
        forecast_routes._ttl_lru_cache_set(cache, lock, "c", {"value": 3}, max_size=2)

        self.assertIn("a", cache)
        self.assertIn("c", cache)
        self.assertNotIn("b", cache)

    def test_ttl_lru_cache_get_expires_stale_entry(self):
        cache = OrderedDict()
        lock = threading.Lock()

        with patch("routes.forecast_routes.time.time", side_effect=[100.0, 150.0]):
            forecast_routes._ttl_lru_cache_set(cache, lock, "x", {"value": 9}, max_size=2)
            value = forecast_routes._ttl_lru_cache_get(cache, lock, "x", ttl_seconds=20)

        self.assertIsNone(value)
        self.assertNotIn("x", cache)

    def test_social_preview_image_endpoint_returns_png(self):
        response = self.client.get("/og-image.png")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "image/png")
        self.assertGreater(len(response.data), 1024)


if __name__ == "__main__":
    unittest.main()
