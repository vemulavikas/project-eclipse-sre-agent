"""Small synchronous client for the Prometheus HTTP API."""

from __future__ import annotations

import json
import math
import os
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


class PrometheusError(Exception):
    """Raised when Prometheus cannot satisfy a monitoring request."""


class PrometheusClient:
    """Query a Prometheus-compatible server using its stable HTTP API."""

    def __init__(self) -> None:
        base_url = os.getenv("PROMETHEUS_URL", "http://localhost:9090").strip().rstrip("/")
        try:
            parsed = urlsplit(base_url)
        except ValueError:
            raise PrometheusError("PROMETHEUS_URL must be a valid HTTP(S) URL.") from None
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise PrometheusError(
                "PROMETHEUS_URL must be an HTTP(S) URL without embedded credentials, query, or fragment."
            )

        try:
            timeout = float(os.getenv("PROMETHEUS_TIMEOUT_SECONDS", "10").strip())
        except ValueError:
            raise PrometheusError("PROMETHEUS_TIMEOUT_SECONDS must be a positive number.") from None
        if not math.isfinite(timeout) or timeout <= 0:
            raise PrometheusError("PROMETHEUS_TIMEOUT_SECONDS must be a positive number.")

        self.base_url = base_url
        self.timeout = timeout
        self.bearer_token = os.getenv("PROMETHEUS_BEARER_TOKEN", "").strip()

    def ready(self) -> bool:
        self._request_text("/-/ready")
        return True

    def query(self, expression: str) -> dict[str, Any]:
        if not expression.strip():
            raise ValueError("A PromQL query is required.")
        return self._request_json("/api/v1/query", {"query": expression})

    def alerts(self) -> list[dict[str, Any]]:
        response = self._request_json("/api/v1/alerts")
        data = response.get("data")
        alerts = data.get("alerts") if isinstance(data, dict) else None
        if not isinstance(alerts, list):
            raise PrometheusError("Prometheus returned an unexpected alerts response.")

        normalized: list[dict[str, Any]] = []
        for item in alerts:
            if not isinstance(item, dict):
                continue
            labels = item.get("labels")
            annotations = item.get("annotations")
            labels = labels if isinstance(labels, dict) else {}
            annotations = annotations if isinstance(annotations, dict) else {}
            normalized.append(
                {
                    "name": str(labels.get("alertname", "Unnamed alert")),
                    "state": str(item.get("state", "unknown")),
                    "severity": str(labels.get("severity", "unknown")),
                    "summary": str(annotations.get("summary", "")),
                    "description": str(annotations.get("description", "")),
                    "active_at": str(item.get("activeAt", "")),
                    "labels": labels,
                }
            )
        return normalized

    def _request_json(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = self._url(path, params)
        request = self._request(url)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
            raise self._connection_error(error) from None

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise PrometheusError("Prometheus returned invalid JSON.") from None
        if not isinstance(payload, dict):
            raise PrometheusError("Prometheus returned an unexpected response format.")
        if payload.get("status") != "success":
            raise PrometheusError("Prometheus reported that the request failed.")
        return payload

    def _request_text(self, path: str) -> str:
        request = self._request(self._url(path))
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except (HTTPError, URLError, TimeoutError, socket.timeout, OSError) as error:
            raise self._connection_error(error) from None

    def _request(self, url: str) -> Request:
        headers = {"Accept": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return Request(url, headers=headers, method="GET")

    def _url(self, path: str, params: dict[str, str] | None = None) -> str:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        return url

    @staticmethod
    def _connection_error(error: Exception) -> PrometheusError:
        if isinstance(error, HTTPError):
            error.close()
            return PrometheusError(f"Prometheus returned HTTP {error.code}.")
        if isinstance(error, (TimeoutError, socket.timeout)):
            return PrometheusError("The Prometheus request timed out.")
        if isinstance(error, URLError) and isinstance(error.reason, (TimeoutError, socket.timeout)):
            return PrometheusError("The Prometheus request timed out.")
        return PrometheusError("Could not connect to the configured Prometheus server.")
