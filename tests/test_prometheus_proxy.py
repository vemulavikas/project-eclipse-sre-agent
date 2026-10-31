from __future__ import annotations

import http.client
import json
import threading
import unittest
from unittest.mock import MagicMock, patch

from monitoring.prometheus_proxy import create_proxy_server, render_prometheus_config


class PrometheusProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_proxy_server("127.0.0.1", 0, "test-token")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def _request(
        self, path: str, authorization: str | None = None
    ) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection(*self.server.server_address)
        headers = {"Authorization": authorization} if authorization else {}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        result = response.status, response.read()
        connection.close()
        return result

    def test_api_requires_token(self) -> None:
        status, _body = self._request("/api/v1/query?query=up")
        self.assertEqual(status, 401)

    @patch("monitoring.prometheus_proxy.urlopen")
    def test_authenticated_api_forwards_request(self, open_url: MagicMock) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{"status":"success"}'
        response.status = 200
        response.headers.get.return_value = "application/json"
        open_url.return_value = response

        status, body = self._request(
            "/api/v1/query?query=up", "Bearer test-token"
        )

        self.assertEqual((status, body), (200, b'{"status":"success"}'))
        self.assertEqual(
            open_url.call_args.args[0].full_url,
            "http://127.0.0.1:9091/api/v1/query?query=up",
        )

    @patch("monitoring.prometheus_proxy.urlopen")
    def test_readiness_route_does_not_require_token(self, open_url: MagicMock) -> None:
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"Prometheus is Ready.\n"
        response.status = 200
        response.headers.get.return_value = "text/plain"
        open_url.return_value = response

        status, body = self._request("/-/ready")

        self.assertEqual((status, body), (200, b"Prometheus is Ready.\n"))

    def test_proxy_requires_configured_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "PROMETHEUS_BEARER_TOKEN"):
            create_proxy_server("127.0.0.1", 0, "")

    def test_config_token_is_quoted_without_environment_expansion_flag(self) -> None:
        token = 'token"with\\characters'
        result = render_prometheus_config(
            "credentials: ${METRICS_BEARER_TOKEN}\n", token
        )
        self.assertEqual(result, f"credentials: {json.dumps(token)}\n")
        self.assertNotIn("--config.expand-env", result)


if __name__ == "__main__":
    unittest.main()
