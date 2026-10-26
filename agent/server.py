"""Local web server for the Prometheus-backed SRE dashboard."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from agent.investigator import Investigator
from agent.workflow import DemoIncidentWorkflow, WorkflowError
from incidents.models import Investigation
from tools.prometheus import PrometheusClient, PrometheusError


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
MAX_REQUEST_BYTES = 64 * 1024
STATIC_FILES = {
    "/": "index.html",
    "/app.js": "app.js",
    "/styles.css": "styles.css",
}


def load_project_env() -> None:
    """Load simple root .env entries without overriding process environment."""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if name and name not in os.environ:
            os.environ[name] = value


def create_server(host: str, port: int) -> ThreadingHTTPServer:
    prometheus = PrometheusClient()
    investigator = Investigator(prometheus)
    workflow = DemoIncidentWorkflow(ROOT / "incidents" / "scenarios" / "checkout_latency.json")

    class SRERequestHandler(BaseHTTPRequestHandler):
        server_version = "SREIncidentDashboard/1.0"

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            if parsed.path in STATIC_FILES:
                self._serve_static(STATIC_FILES[parsed.path])
                return
            if parsed.path == "/api/health":
                self._health()
                return
            if parsed.path == "/api/alerts":
                self._alerts()
                return
            if parsed.path == "/api/query":
                self._query(parse_qs(parsed.query).get("query", [""])[0])
                return
            if parsed.path == "/api/scenarios":
                self._json_response(
                    200,
                    {
                        "scenarios": [
                            {
                                "id": "checkout-latency",
                                "title": "Checkout latency after release",
                                "mode": "simulated",
                            }
                        ]
                    },
                )
                return
            if parsed.path.startswith("/api/report/"):
                session_id = parsed.path.removeprefix("/api/report/")
                try:
                    self._text_response(200, workflow.report(session_id), "text/markdown; charset=utf-8")
                except WorkflowError as error:
                    self._json_response(404, {"error": str(error)})
                return
            self._json_response(404, {"error": "Not found."})

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if path not in {"/api/investigate", "/api/actions/approve", "/api/verify"}:
                self._json_response(404, {"error": "Not found."})
                return
            try:
                body = self._read_json_body()
            except ValueError as error:
                self._json_response(400, {"error": str(error)})
                return

            if path == "/api/actions/approve":
                self._approve_action(body)
                return
            if path == "/api/verify":
                self._verify_recovery(body)
                return

            mode = body.get("mode", "live")
            if mode == "demo":
                incident = body.get("incident", "")
                ai_assist = body.get("ai_assist", False)
                if not isinstance(incident, str) or len(incident) > 4000:
                    self._json_response(400, {"error": "Incident details must be text of at most 4,000 characters."})
                    return
                if not isinstance(ai_assist, bool):
                    self._json_response(400, {"error": "ai_assist must be a boolean."})
                    return
                try:
                    session = workflow.investigate(incident)
                    if ai_assist:
                        session = asyncio.run(
                            workflow.add_ai_assessment(session["session_id"])
                        )
                    self._json_response(200, session)
                except WorkflowError as error:
                    self._json_response(500, {"error": str(error)})
                return
            if mode != "live":
                self._json_response(400, {"error": "Mode must be 'demo' or 'live'."})
                return

            query = body.get("query")
            incident = body.get("incident", "")
            if not isinstance(query, str) or not query.strip():
                self._json_response(400, {"error": "A PromQL query is required."})
                return
            if len(query) > 2000:
                self._json_response(400, {"error": "The PromQL query must be 2,000 characters or fewer."})
                return
            if not isinstance(incident, str) or len(incident) > 4000:
                self._json_response(400, {"error": "Incident details must be text of at most 4,000 characters."})
                return
            try:
                result = asyncio.run(investigator.investigate(query.strip(), incident.strip()))
            except PrometheusError as error:
                self._json_response(502, {"error": str(error)})
                return
            except ValueError as error:
                self._json_response(502, {"error": str(error)})
                return
            self._json_response(200, _investigation_json(result))

        def _approve_action(self, body: dict[str, Any]) -> None:
            session_id = body.get("session_id")
            action_id = body.get("action_id")
            approved = body.get("approved")
            approver = body.get("approver")
            rationale = body.get("rationale")
            if (
                not isinstance(session_id, str)
                or not isinstance(action_id, str)
                or not isinstance(approved, bool)
                or not isinstance(approver, str)
                or not isinstance(rationale, str)
            ):
                self._json_response(400, {"error": "Approval requires session_id, action_id, approved, approver, and rationale."})
                return
            try:
                session = workflow.approve_action(
                    session_id, action_id, approved, approver, rationale
                )
            except WorkflowError as error:
                self._json_response(409, {"error": str(error)})
                return
            self._json_response(200, session)

        def _verify_recovery(self, body: dict[str, Any]) -> None:
            session_id = body.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                self._json_response(400, {"error": "session_id is required."})
                return
            try:
                session = workflow.verify_recovery(session_id)
            except WorkflowError as error:
                self._json_response(409, {"error": str(error)})
                return
            self._json_response(200, session)

        def _health(self) -> None:
            try:
                prometheus.ready()
            except PrometheusError as error:
                self._json_response(503, {"status": "disconnected", "error": str(error)})
                return
            self._json_response(200, {"status": "connected"})

        def _alerts(self) -> None:
            try:
                alerts = prometheus.alerts()
            except PrometheusError as error:
                self._json_response(502, {"error": str(error)})
                return
            self._json_response(200, {"alerts": alerts})

        def _query(self, expression: str) -> None:
            if not expression.strip():
                self._json_response(400, {"error": "A PromQL query is required."})
                return
            if len(expression) > 2000:
                self._json_response(400, {"error": "The PromQL query must be 2,000 characters or fewer."})
                return
            try:
                result = prometheus.query(expression.strip())
            except PrometheusError as error:
                self._json_response(502, {"error": str(error)})
                return
            self._json_response(200, result)

        def _read_json_body(self) -> dict[str, Any]:
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length or "")
            except ValueError:
                raise ValueError("A valid Content-Length header is required.") from None
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("Request body is too large.")
            try:
                body = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ValueError("Request body must be valid JSON.") from None
            if not isinstance(body, dict):
                raise ValueError("Request body must be a JSON object.")
            return body

        def _serve_static(self, filename: str) -> None:
            path = FRONTEND / filename
            try:
                content = path.read_bytes()
            except OSError:
                self._json_response(500, {"error": "The dashboard asset could not be read."})
                return
            content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            self._send_headers(200, content_type, len(content))
            self.wfile.write(content)

        def _json_response(self, status: int, value: dict[str, Any]) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self._send_headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _text_response(self, status: int, value: str, content_type: str) -> None:
            body = value.encode("utf-8")
            self._send_headers(status, content_type, len(body))
            self.wfile.write(body)

        def _send_headers(self, status: int, content_type: str, length: int) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; connect-src 'self'; style-src 'self'; "
                "script-src 'self'; img-src 'self' data:; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()

    return ThreadingHTTPServer((host, port), SRERequestHandler)


def _investigation_json(result: Investigation) -> dict[str, Any]:
    return {
        "query": result.query,
        "metric_result": result.metric_result,
        "active_alerts": result.active_alerts,
        "assessment": result.assessment,
        "ai_assessment": result.ai_assessment,
        "ai_error": result.ai_error,
    }


def main() -> None:
    load_project_env()
    host = os.getenv("HOST", "127.0.0.1").strip()
    try:
        port = int(os.getenv("PORT", "8000").strip())
    except ValueError:
        raise SystemExit("PORT must be an integer.") from None
    if not host or not 0 < port < 65536:
        raise SystemExit("HOST must not be empty and PORT must be between 1 and 65535.")

    try:
        server = create_server(host, port)
    except PrometheusError as error:
        raise SystemExit(str(error)) from None
    print(f"SRE incident dashboard listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down the SRE incident dashboard.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
