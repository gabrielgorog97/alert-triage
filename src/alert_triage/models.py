from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Outcome(StrEnum):
    TRIAGED = "triaged"
    REOPENED = "reopened"
    DUPLICATE = "duplicate"
    MANUAL_REVIEW = "manual_review"
    REJECTED = "rejected"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class Priority(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


@dataclass(frozen=True)
class Alert:
    alert_id: str
    name: str
    service: str
    environment: str
    status: str
    started_at: datetime
    value: float | None = None
    threshold: float | None = None
    description: str | None = None

    @property
    def fingerprint(self) -> str:
        return f"{self.name}|{self.service}|{self.environment}"


@dataclass(frozen=True)
class Service:
    name: str
    owner: str
    escalation: str
    customer_facing: bool
    environments: tuple[str, ...]
    runbook: str


@dataclass
class TriageResult:
    alert_id: str
    outcome: Outcome
    fingerprint: str | None = None
    description: str | None = None
    opsgenie_id: str | None = None
    priority: Priority | None = None
    impact: str | None = None
    owner: str | None = None
    escalation: str | None = None
    responder: str | None = None
    ack_timeout_minutes: int | None = None
    page: bool = False
    reasons: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    runbook: str | None = None
