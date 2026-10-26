"""Combine Prometheus observations with optional AI-assisted triage."""

from __future__ import annotations

import json
from typing import Any

from gateway import AIGatewayError, ai_gateway
from incidents.models import Investigation
from tools.prometheus import PrometheusClient


class Investigator:
    """Gather live metrics and alerts, then produce a cautious assessment."""

    def __init__(self, prometheus: PrometheusClient) -> None:
        self.prometheus = prometheus

    async def investigate(self, query: str, incident: str = "") -> Investigation:
        metric_response = self.prometheus.query(query)
        result = metric_response.get("data", {})
        if not isinstance(result, dict):
            raise ValueError("Prometheus returned an unexpected query result.")
        metric_result = {
            "result_type": result.get("resultType", "unknown"),
            "result": result.get("result", []),
        }
        if not isinstance(metric_result["result"], list):
            raise ValueError("Prometheus returned an unexpected query result.")
        alerts = self.prometheus.alerts()
        assessment = self._local_assessment(metric_result, alerts)
        ai_assessment: str | None = None
        ai_error: str | None = None

        try:
            ai_assessment = await self._generate_ai_assessment(
                query, incident, metric_result, alerts
            )
        except AIGatewayError as error:
            ai_error = str(error)

        return Investigation(
            query=query,
            metric_result=metric_result,
            active_alerts=alerts,
            assessment=assessment,
            ai_assessment=ai_assessment,
            ai_error=ai_error,
        )

    @staticmethod
    def _local_assessment(
        metric_result: dict[str, Any], alerts: list[dict[str, Any]]
    ) -> str:
        series_count = len(metric_result["result"])
        alert_count = len(alerts)
        if series_count == 0:
            metric_note = "The query returned no matching time series."
        else:
            metric_note = (
                f"The query returned {series_count} matching "
                f"{'time series' if series_count != 1 else 'time series'}."
            )
        if alert_count == 0:
            alert_note = "Prometheus reports no active alerts."
        else:
            alert_note = f"Prometheus reports {alert_count} alert(s)."
        return f"{metric_note} {alert_note} Review the observed values and alert labels before taking action."

    @staticmethod
    async def _generate_ai_assessment(
        query: str,
        incident: str,
        metric_result: dict[str, Any],
        alerts: list[dict[str, Any]],
    ) -> str:
        context = json.dumps(
            {
                "incident": incident,
                "promql": query,
                "query_result": metric_result,
                "active_alerts": alerts[:25],
            },
            ensure_ascii=True,
        )
        response = await ai_gateway.generate(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an SRE incident triage assistant. Analyze only the supplied "
                        "Prometheus observations. Separate evidence from hypotheses, do not "
                        "claim a root cause without evidence, and recommend safe read-only "
                        "checks before any mitigation."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Assess this incident using the provided data:\n{context}",
                },
            ],
            max_tokens=500,
            temperature=0.2,
        )
        choices: Any = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AIGatewayError("The AI provider returned no assistant message.")
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise AIGatewayError("The AI provider returned an empty assistant message.")
        return content.strip()
