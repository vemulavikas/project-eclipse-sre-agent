"""Run Prometheus behind a bearer-authenticated, read-only HTTP proxy."""

from __future__ import annotations

import hmac
import os
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


UPSTREAM = "http://127.0.0.1:9091"


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
    token = os.getenv("PROMETHEUS_BEARER_TOKEN", "").strip()
    if not token:
        raise SystemExit("PROMETHEUS_BEARER_TOKEN must be configured.")

    prometheus = subprocess.Popen(
        [
            "prometheus",
            "--config.file=/etc/prometheus/prometheus.yml",
            "--config.expand-env",
            "--storage.tsdb.path=/prometheus",
            "--web.listen-address=127.0.0.1:9091",
        ]
    )
    try:
        _wait_for_prometheus(prometheus)
        port = int(os.getenv("PORT", "10000"))
        server = create_proxy_server("0.0.0.0", port, token)
    except (OSError, RuntimeError, ValueError) as error:
        prometheus.terminate()
        prometheus.wait(timeout=10)
        raise SystemExit(str(error)) from None

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        prometheus.terminate()
        try:
            prometheus.wait(timeout=10)
        except subprocess.TimeoutExpired:
            prometheus.kill()
            prometheus.wait()


if __name__ == "__main__":
    main()
