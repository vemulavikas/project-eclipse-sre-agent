"""Public response shapes for incident investigations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Investigation:
    query: str
    metric_result: dict[str, Any]
    active_alerts: list[dict[str, Any]]
    assessment: str
    ai_assessment: str | None
    ai_error: str | None
