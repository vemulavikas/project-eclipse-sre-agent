from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agent.server import create_server
from agent.workflow import IncidentWorkflow
from gateway import MissingAPIKeyError


class _Prometheus:
    def ready(self) -> bool:
        return True

    def alerts(self) -> list[dict[str, str]]:
        return []

    def query(self, expression: str) -> dict[str, object]:
        return {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [{"metric": {"job": "checkout"}, "value": [1, "1"]}],
            },
        }


class DashboardServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prometheus = _Prometheus()
        self.server_patcher = patch("agent.server.PrometheusClient", return_value=self.prometheus)
        self.server_patcher.start()
        self.addCleanup(self.server_patcher.stop)
        self.server = create_server("127.0.0.1", 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop_server)

    def _stop_server(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def _request(
        self, method: str, path: str, body: dict[str, object] | None = None
    ) -> tuple[int, dict[str, object] | bytes]:
        connection = http.client.HTTPConnection(*self.server.server_address)
        encoded_body = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if encoded_body is not None else {}
        connection.request(method, path, body=encoded_body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        content_type = response.getheader("Content-Type", "")
        connection.close()
        if content_type.startswith("application/json"):
            return response.status, json.loads(payload)
        return response.status, payload

    def test_dashboard_assets_and_health_are_served(self) -> None:
        status, page = self._request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Signal Desk", page)
        self.assertEqual(self._request("GET", "/api/health"), (200, {"status": "connected"}))

    @patch(
        "agent.investigator.ai_gateway.generate",
        new_callable=AsyncMock,
        side_effect=MissingAPIKeyError("NVIDIA_API_KEY is required for the selected provider."),
    )
    def test_investigation_returns_prometheus_evidence_when_ai_is_unavailable(
        self, _generate: AsyncMock
    ) -> None:
        status, payload = self._request(
            "POST",
            "/api/investigate",
            {"query": "up", "incident": "Checkout is slow"},
        )
        self.assertEqual(status, 200)
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["query"], "up")
        self.assertEqual(payload["metric_result"]["result"][0]["metric"]["job"], "checkout")
        self.assertEqual(payload["active_alerts"], [])
        self.assertIn("NVIDIA_API_KEY", payload["ai_error"])

    def test_investigation_requires_promql(self) -> None:
        status, payload = self._request("POST", "/api/investigate", {"query": ""})
        self.assertEqual(status, 400)
        self.assertEqual(payload, {"error": "A PromQL query is required."})

    def test_demo_workflow_handles_zero_baseline_metrics(self) -> None:
        scenario = {
            "id": "zero-latency",
            "title": "Zero baseline incident",
            "service": "checkout",
            "summary": "Regression with no prior latency baseline.",
            "query": "sum(rate(queries_total[5m]))",
            "metrics": {
                "baseline": {"error_rate": 0.0, "p95_latency_seconds": 0.0},
                "current": {"error_rate": 0.12, "p95_latency_seconds": 0.6},
                "after_rollback": {"error_rate": 0.01, "p95_latency_seconds": 0.1, "active_alerts": 0, "version": "v1"},
            },
            "events": [
                {
                    "id": "deploy-1",
                    "source": "deployment",
                    "timestamp": "2026-10-26T14:02:00Z",
                    "title": "release deployed",
                    "details": "release deployed",
                    "version": "v2",
                }
            ],
            "previous_version": "v1",
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(scenario, handle)
            temp_path = Path(handle.name)
        self.addCleanup(lambda: temp_path.unlink(missing_ok=True))
        workflow = IncidentWorkflow(temp_path)
        session = workflow.investigate("Regression during rollout")
        self.assertEqual(session["metrics"]["p95_latency_multiplier"], 1.0)
        self.assertIn("infinite", session["hypotheses"][0]["reasoning"])

    def test_demo_requires_approval_then_verifies_and_generates_report(self) -> None:
        status, session = self._request(
            "POST",
            "/api/investigate",
            {"mode": "demo", "incident": "Checkout latency increased"},
        )
        self.assertEqual(status, 200)
        self.assertIsInstance(session, dict)
        self.assertEqual(session["mode"], "demo")
        self.assertEqual(
            {source["name"] for source in session["sources"]},
            {"alert", "dependency", "deployment", "git", "logs", "metrics"},
        )
        timestamps = [event["timestamp"] for event in session["timeline"]]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertGreaterEqual(session["hypotheses"][0]["confidence"], 0.8)

        session_id = session["session_id"]
        status, error = self._request(
            "POST", "/api/verify", {"session_id": session_id}
        )
        self.assertEqual(status, 409)
        self.assertIn("blocked", error["error"])

        status, approved = self._request(
            "POST",
            "/api/actions/approve",
            {
                "session_id": session_id,
                "action_id": "rollback-checkout",
                "approved": True,
                "approver": "On-call SRE",
                "rationale": "Rollback is approved to restore checkout health.",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(approved["action_result"]["mode"], "simulation")

        status, verified = self._request(
            "POST", "/api/verify", {"session_id": session_id}
        )
        self.assertEqual(status, 200)
        self.assertEqual(verified["verification"]["status"], "passed")
        self.assertTrue(all(check["passed"] for check in verified["verification"]["checks"]))

        status, report = self._request("GET", f"/api/report/{session_id}")
        self.assertEqual(status, 200)
        self.assertIn(b"Post-incident report", report)
        self.assertIn(b"Recovery verification", report)

    def test_rejected_remediation_escalates_and_still_generates_report(self) -> None:
        status, session = self._request(
            "POST", "/api/investigate", {"mode": "demo"}
        )
        self.assertEqual(status, 200)
        session_id = session["session_id"]

        status, rejected = self._request(
            "POST",
            "/api/actions/approve",
            {
                "session_id": session_id,
                "action_id": "rollback-checkout",
                "approved": False,
                "approver": "On-call SRE",
                "rationale": "Escalating for incident commander review.",
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(rejected["escalation"]["status"], "escalated")

        status, report = self._request("GET", f"/api/report/{session_id}")
        self.assertEqual(status, 200)
        self.assertIn(b"escalated for human review", report)
        self.assertIn(b"Incident commander", report)

    @patch(
        "agent.workflow.ai_gateway.generate",
        new_callable=AsyncMock,
        return_value={"choices": [{"message": {"content": "The release timing is a plausible cause."}}]},
    )
    def test_demo_ai_second_opinion_is_opt_in_and_recorded(
        self, _generate: AsyncMock
    ) -> None:
        status, session = self._request(
            "POST", "/api/investigate", {"mode": "demo", "ai_assist": True}
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            session["ai_assessment"], "The release timing is a plausible cause."
        )
        _generate.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
