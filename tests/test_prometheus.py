from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from tools.prometheus import PrometheusClient, PrometheusError


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class PrometheusClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(
            os.environ,
            {
                "PROMETHEUS_URL": "http://prometheus.example:9090",
                "PROMETHEUS_TIMEOUT_SECONDS": "2",
                "PROMETHEUS_BEARER_TOKEN": "prom-test-token",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = PrometheusClient()

    @patch("tools.prometheus.urlopen")
    def test_query_encodes_promql_and_uses_bearer_auth(self, open_url: object) -> None:
        response = _Response(
            json.dumps({"status": "success", "data": {"resultType": "vector", "result": []}}).encode()
        )
        open_url.return_value = response

        result = self.client.query('up{job="api"}')

        request = open_url.call_args.args[0]
        self.assertIn("query=up%7Bjob%3D%22api%22%7D", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer prom-test-token")
        self.assertEqual(result["status"], "success")

    @patch("tools.prometheus.urlopen")
    def test_alerts_normalize_labels_and_annotations(self, open_url: object) -> None:
        payload = {
            "status": "success",
            "data": {
                "alerts": [
                    {
                        "labels": {"alertname": "HighLatency", "severity": "warning"},
                        "annotations": {"summary": "Latency elevated"},
                        "state": "firing",
                        "activeAt": "2026-10-26T10:00:00Z",
                    }
                ]
            },
        }
        open_url.return_value = _Response(json.dumps(payload).encode())

        alerts = self.client.alerts()

        self.assertEqual(alerts[0]["name"], "HighLatency")
        self.assertEqual(alerts[0]["summary"], "Latency elevated")
        self.assertEqual(alerts[0]["state"], "firing")

    @patch("tools.prometheus.urlopen")
    def test_query_rejects_provider_error_response(self, open_url: object) -> None:
        open_url.return_value = _Response(b'{"status":"error","error":"bad query"}')

        with self.assertRaises(PrometheusError):
            self.client.query("up")

    def test_rejects_url_with_embedded_credentials(self) -> None:
        with patch.dict(os.environ, {"PROMETHEUS_URL": "http://user:password@host:9090"}):
            with self.assertRaises(PrometheusError):
                PrometheusClient()


if __name__ == "__main__":
    unittest.main()
