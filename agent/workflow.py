"""Deterministic incident workflow for the Eclipse simulated-incident track."""

from __future__ import annotations

import json
import math
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from gateway import AIGatewayError, ai_gateway


class WorkflowError(Exception):
    """Raised when an incident workflow transition is invalid."""


class IncidentWorkflow:
    """Analyze a fixture, gate a simulated action, and verify its outcome."""

    def __init__(self, scenario_path: Path) -> None:
        try:
            scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WorkflowError("The simulated incident scenario could not be loaded.") from error
        if not isinstance(scenario, dict) or not isinstance(scenario.get("events"), list):
            raise WorkflowError("The simulated incident scenario has an invalid format.")
        self._scenario = scenario
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def investigate(self, incident: str = "") -> dict[str, Any]:
        events = sorted(
            self._scenario["events"],
            key=lambda event: self._parse_timestamp(event.get("timestamp")),
        )
        source_groups: dict[str, int] = {}
        for event in events:
            source = str(event.get("source", "unknown"))
            source_groups[source] = source_groups.get(source, 0) + 1

        metrics = self._scenario.get("metrics", {})
        before = metrics.get("baseline", {}) if isinstance(metrics, dict) else {}
        current = metrics.get("current", {}) if isinstance(metrics, dict) else {}
        before_error_rate = self._coerce_float(before.get("error_rate"), 0.0)
        current_error_rate = self._coerce_float(current.get("error_rate"), before_error_rate)
        before_latency = self._coerce_float(before.get("p95_latency_seconds"), 0.0)
        current_latency = self._coerce_float(current.get("p95_latency_seconds"), before_latency)
        error_increase = current_error_rate - before_error_rate
        latency_increase = self._safe_ratio(current_latency, before_latency)
        relevant_ids = [
            event["id"]
            for event in events
            if event.get("source") in {"git", "deployment", "logs", "dependency", "metrics", "alert"}
        ]
        hypotheses = [
            {
                "title": "Release v2.3.7 introduced a checkout payment-client regression",
                "confidence": 0.87,
                "confidence_label": "High",
                "reasoning": (
                    f"The relevant code change preceded the deployment; payment timeouts "
                    f"began 3 minutes after rollout. The error ratio rose by "
                    f"{error_increase:.1%} and p95 latency increased {self._format_multiplier(latency_increase)}, "
                    "while the dependency health check remained available."
                ),
                "evidence_ids": relevant_ids,
                "caveat": "Temporal correlation and the shared fixture support this hypothesis; they do not prove causation.",
            },
            {
                "title": "A payment-api outage is the primary cause",
                "confidence": 0.24,
                "confidence_label": "Low",
                "reasoning": (
                    "Payment calls are degraded, but the dependency health check remains "
                    "available and a checkout release immediately preceded the symptoms."
                ),
                "evidence_ids": ["dependency-1", "deploy-1", "log-1"],
                "caveat": "Dependency-level telemetry is limited in this simulated dataset.",
            },
        ]

        session_id = str(uuid.uuid4())
        plan = [
            {
                "id": "inspect-release",
                "title": "Compare v2.3.7 payment retry changes with v2.3.6",
                "kind": "read_only",
                "impact": "low",
                "approval_required": False,
                "status": "ready",
                "description": "Review the cited commit and deployment metadata; no production changes.",
            },
            {
                "id": "rollback-checkout",
                "title": f"Roll back checkout to {self._scenario['previous_version']}",
                "kind": "simulated_change",
                "impact": "high",
                "approval_required": True,
                "status": "awaiting_approval",
                "description": (
                    "Simulation only. This action does not call a deployment system. "
                    "It must be explicitly approved before execution."
                ),
            },
            {
                "id": "verify-recovery",
                "title": "Re-check error rate, latency, alerts, and dependencies",
                "kind": "verification",
                "impact": "low",
                "approval_required": False,
                "status": "blocked",
                "description": "Run after the simulated rollback has completed.",
            },
        ]
        multiplier = 1.0 if math.isinf(latency_increase) else round(latency_increase, 2)
        session = {
            "session_id": session_id,
            "mode": "demo",
            "scenario": self._scenario["id"],
            "title": self._scenario["title"],
            "service": self._scenario["service"],
            "incident": incident.strip() or self._scenario["summary"],
            "query": self._scenario["query"],
            "timeline": events,
            "sources": [
                {"name": name, "event_count": count}
                for name, count in sorted(source_groups.items())
            ],
            "metrics": {
                "baseline": before,
                "current": current,
                "error_rate_increase": round(error_increase, 4),
                "p95_latency_multiplier": multiplier,
            },
            "hypotheses": hypotheses,
            "plan": plan,
            "approval": None,
            "escalation": None,
            "action_result": None,
            "verification": None,
            "report": None,
            "ai_assessment": None,
            "ai_error": None,
        }
        with self._lock:
            self._sessions[session_id] = session
        return self._copy(session)

    async def add_ai_assessment(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        context = json.dumps(
            {
                "incident": session["incident"],
                "service": session["service"],
                "timeline": session["timeline"],
                "metrics": session["metrics"],
                "current_hypotheses": session["hypotheses"],
            },
            ensure_ascii=True,
        )
        try:
            response = await ai_gateway.generate(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an SRE incident investigation assistant. Assess only the "
                            "provided simulated evidence. Separate observed facts from "
                            "hypotheses, state uncertainty, and suggest read-only checks. "
                            "Do not authorize or execute remediation."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Provide a concise second opinion on this incident:\n{context}",
                    },
                ],
                max_tokens=400,
                temperature=0.2,
            )
            choices = response.get("choices")
            first = choices[0] if isinstance(choices, list) and choices else None
            message = first.get("message") if isinstance(first, dict) else None
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, str) or not content.strip():
                raise AIGatewayError("The AI provider returned no assistant message.")
            assessment, ai_error = content.strip(), None
        except AIGatewayError as gateway_error:
            assessment = None
            ai_error = str(gateway_error)

        with self._lock:
            stored = self._require_session(session_id)
            stored["ai_assessment"] = assessment
            stored["ai_error"] = ai_error
            return self._copy(stored)

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise WorkflowError("Incident session not found.")
            return self._copy(session)

    def approve_action(
        self,
        session_id: str,
        action_id: str,
        approved: bool,
        approver: str,
        rationale: str,
    ) -> dict[str, Any]:
        with self._lock:
            session = self._require_session(session_id)
            action = self._require_action(session, action_id)
            if action["status"] != "awaiting_approval":
                raise WorkflowError("This action is not awaiting approval.")
            if action["kind"] != "simulated_change" or not action["approval_required"]:
                raise WorkflowError("Only approval-gated simulated actions can be approved here.")
            if not approved:
                action["status"] = "rejected"
                session["approval"] = {
                    "approved": False,
                    "approver": approver.strip(),
                    "rationale": rationale.strip(),
                }
                session["escalation"] = {
                    "status": "escalated",
                    "target": "Incident commander",
                    "reason": rationale.strip() or "High-impact remediation was not approved.",
                    "escalated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
                session["report"] = self._build_report(session)
                return self._copy(session)
            if len(approver.strip()) < 2 or len(rationale.strip()) < 8:
                raise WorkflowError("Approval requires an approver name and an 8-character rationale.")
            session["approval"] = {
                "approved": True,
                "approver": approver.strip(),
                "rationale": rationale.strip(),
                "approved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
            action["status"] = "approved"
            action["status"] = "executed"
            session["action_result"] = {
                "status": "executed",
                "mode": "simulation",
                "result": f"Simulated rollback completed; checkout now runs {self._scenario['previous_version']}.",
            }
            verify_step = self._require_action(session, "verify-recovery")
            verify_step["status"] = "ready"
            return self._copy(session)

    def verify_recovery(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            session = self._require_session(session_id)
            rollback = self._require_action(session, "rollback-checkout")
            if rollback["status"] != "executed":
                raise WorkflowError("Recovery verification is blocked until the approved action executes.")
            metrics = self._scenario.get("metrics", {})
            after = metrics.get("after_rollback", {}) if isinstance(metrics, dict) else {}
            error_rate = self._coerce_float(after.get("error_rate"), float("inf"))
            latency_seconds = self._coerce_float(after.get("p95_latency_seconds"), float("inf"))
            active_alerts = 0
            if "active_alerts" in after:
                try:
                    active_alerts = int(after["active_alerts"])
                except (TypeError, ValueError):
                    active_alerts = 1
            restored_version = str(after.get("version", ""))
            checks = [
                {
                    "name": "Error rate below 5% threshold",
                    "passed": error_rate < 0.05,
                    "observed": f"{error_rate:.1%}",
                },
                {
                    "name": "p95 latency below 1 second",
                    "passed": latency_seconds < 1,
                    "observed": f"{latency_seconds:.2f}s",
                },
                {
                    "name": "No active firing alerts after recovery",
                    "passed": active_alerts == 0,
                    "observed": f"{active_alerts} active alerts",
                },
                {
                    "name": "Previous known-good version restored",
                    "passed": restored_version == self._scenario.get("previous_version", ""),
                    "observed": restored_version,
                },
            ]
            verification = {
                "status": "passed" if all(check["passed"] for check in checks) else "failed",
                "checks": checks,
                "verified_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "mode": "simulation",
            }
            session["verification"] = verification
            self._require_action(session, "verify-recovery")["status"] = (
                "verified" if verification["status"] == "passed" else "failed"
            )
            session["report"] = self._build_report(session)
            return self._copy(session)

    def report(self, session_id: str) -> str:
        with self._lock:
            session = self._require_session(session_id)
            if not session["report"]:
                raise WorkflowError("The post-incident report is available after recovery verification.")
            return session["report"]

    def _require_session(self, session_id: str) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if session is None:
            raise WorkflowError("Incident session not found.")
        return session

    @staticmethod
    def _require_action(session: dict[str, Any], action_id: str) -> dict[str, Any]:
        for action in session["plan"]:
            if action["id"] == action_id:
                return action
        raise WorkflowError("Remediation step not found.")

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        if isinstance(value, bool):
            return float(value)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return float(default)
        if math.isnan(number) or math.isinf(number):
            return float(default)
        return number

    @staticmethod
    def _safe_ratio(current: float, baseline: float) -> float:
        if baseline == 0:
            return math.inf if current > 0 else 1.0
        return current / baseline

    @staticmethod
    def _format_multiplier(value: float) -> str:
        if math.isinf(value):
            return "infinite×"
        if value == 0:
            return "0.0×"
        return f"{value:.1f}×"

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        if not isinstance(value, str):
            return datetime.min
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @staticmethod
    def _copy(session: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(session))

    @staticmethod
    def _build_report(session: dict[str, Any]) -> str:
        timeline = "\n".join(
            f"- {event['timestamp']} [{event['source']}] {event['title']}: {event['details']}"
            for event in session["timeline"]
        )
        hypotheses = "\n".join(
            f"- **{hypothesis['title']}** — {hypothesis['confidence_label']} "
            f"({hypothesis['confidence']:.0%}). {hypothesis['reasoning']} "
            f"Caveat: {hypothesis['caveat']}"
            for hypothesis in session["hypotheses"]
        )
        verification = session["verification"]
        escalation = session["escalation"]
        if verification:
            checks = "\n".join(
                f"- [{'x' if check['passed'] else ' '}] {check['name']}: {check['observed']}"
                for check in verification["checks"]
            )
            outcome = f"recovery verification {verification['status']} ({verification['mode']})"
        else:
            checks = "Recovery verification not run; remediation approval was declined."
            outcome = "escalated for human review"
        approval = session["approval"]
        return (
            f"# Post-incident report: {session['title']}\n\n"
            f"- **Service:** {session['service']}\n"
            f"- **Incident:** {session['incident']}\n"
            f"- **Outcome:** {outcome}\n"
            f"- **Approved by:** {approval['approver'] if approval else 'No approval'}\n\n"
            f"## Evidence timeline\n{timeline}\n\n"
            f"## Root-cause hypotheses\n{hypotheses}\n\n"
            + (
                f"## Optional AI second opinion\n{session['ai_assessment']}\n\n"
                if session["ai_assessment"]
                else ""
            )
            + f"## Remediation and approval\n"
            f"{session['action_result']['result'] if session['action_result'] else 'No action executed.'}\n\n"
            f"Approval rationale: {approval['rationale'] if approval else 'None'}\n\n"
            f"## Recovery verification\n{checks}\n\n"
            + (
                f"## Escalation\nEscalated to {escalation['target']}: {escalation['reason']}\n\n"
                if escalation
                else ""
            )
            + "## Follow-up\n"
            "Validate the suspected payment retry change against production deployment and source-control "
            "records before treating this simulated hypothesis as a confirmed root cause.\n"
        )
