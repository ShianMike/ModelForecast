import unittest
from unittest.mock import Mock, patch

import requests

from forecast import open_meteo


class OpenMeteoTests(unittest.TestCase):
    def test_request_with_retry_retries_transport_errors(self):
        ok_response = Mock(status_code=200)

        with patch.object(
            open_meteo._session,
            "get",
            side_effect=[requests.exceptions.Timeout("boom"), ok_response],
        ) as mock_get, patch("forecast.open_meteo.time.sleep") as sleep_mock, patch(
            "forecast.open_meteo.random.uniform",
            return_value=0.0,
        ):
            response = open_meteo._request_with_retry("https://example.com")

        self.assertIs(response, ok_response)
        self.assertEqual(mock_get.call_count, 2)
        sleep_mock.assert_called_once_with(1.0)

    def test_extract_hour_rejects_negative_index(self):
        payload = {
            "lats": [0.0],
            "lons": [0.0],
            "hourly_grid": [[[10.0, 20.0, 30.0]]],
            "unit": "x",
        }

        with self.assertRaisesRegex(ValueError, "Forecast hour must be greater than or equal to 0."):
            open_meteo._extract_hour(payload, "gfs", "temperature_2m", -1)


if __name__ == "__main__":
    unittest.main()
