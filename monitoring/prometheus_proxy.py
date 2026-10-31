"""Run Prometheus behind a bearer-authenticated, read-only HTTP proxy."""

from __future__ import annotations

import hmac
import json
import os
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


UPSTREAM = "http://127.0.0.1:9091"
CONFIG_TEMPLATE = "/etc/prometheus/prometheus.yml"


def create_proxy_server(
    host: str,
    port: int,
    token: str,
    upstream: str = UPSTREAM,
) -> ThreadingHTTPServer:
    if not token:
        raise ValueError("PROMETHEUS_BEARER_TOKEN must be configured.")

    class PrometheusProxyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path != "/-/ready" and not hmac.compare_digest(
                self.headers.get("Authorization", "").encode("utf-8"),
                f"Bearer {token}".encode("utf-8"),
            ):
                self.send_error(401, "Bearer token required.")
                return

            request = Request(f"{upstream}{self.path}", method="GET")
            try:
                with urlopen(request, timeout=15) as response:
                    body = response.read()
                    status = response.status
                    content_type = response.headers.get(
                        "Content-Type", "application/octet-stream"
                    )
            except HTTPError as error:
                body = error.read()
                status = error.code
                content_type = error.headers.get(
                    "Content-Type", "application/octet-stream"
                )
                error.close()
            except (URLError, TimeoutError, OSError):
                self.send_error(502, "Prometheus is temporarily unavailable.")
                return

            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), PrometheusProxyHandler)


def render_prometheus_config(template: str, metrics_token: str) -> str:
    if not metrics_token:
        raise ValueError("METRICS_BEARER_TOKEN must be configured.")
    return template.replace(
        "${METRICS_BEARER_TOKEN}", json.dumps(metrics_token)
    )


def _wait_for_prometheus(process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Prometheus exited before becoming ready.")
        try:
            with urlopen(f"{UPSTREAM}/-/ready", timeout=2):
                return
        except (URLError, TimeoutError, OSError):
            time.sleep(1)
    raise RuntimeError("Prometheus did not become ready within 60 seconds.")


def main() -> None:
    prometheus_token = os.getenv("PROMETHEUS_BEARER_TOKEN", "").strip()
    metrics_token = os.getenv("METRICS_BEARER_TOKEN", "").strip()
    if not prometheus_token:
        raise SystemExit("PROMETHEUS_BEARER_TOKEN must be configured.")
    if not metrics_token:
        raise SystemExit("METRICS_BEARER_TOKEN must be configured.")

    config_path: str | None = None
    prometheus: subprocess.Popen[bytes] | None = None
    try:
        with open(CONFIG_TEMPLATE, encoding="utf-8") as template_file:
            config_contents = render_prometheus_config(
                template_file.read(), metrics_token
            )
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".yml", delete=False
        ) as config_file:
            config_file.write(config_contents)
            config_path = config_file.name

        prometheus = subprocess.Popen(
            [
                "prometheus",
                f"--config.file={config_path}",
                "--storage.tsdb.path=/prometheus",
                "--web.listen-address=127.0.0.1:9091",
            ]
        )
        _wait_for_prometheus(prometheus)
        port = int(os.getenv("PORT", "10000"))
        server = create_proxy_server("0.0.0.0", port, prometheus_token)
    except (OSError, RuntimeError, ValueError) as error:
        if prometheus:
            prometheus.terminate()
            prometheus.wait(timeout=10)
        if config_path:
            os.unlink(config_path)
        raise SystemExit(str(error)) from None

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        if prometheus:
            prometheus.terminate()
        try:
            if prometheus:
                prometheus.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if prometheus:
                prometheus.kill()
                prometheus.wait()
        if config_path:
            os.unlink(config_path)


if __name__ == "__main__":
    main()
