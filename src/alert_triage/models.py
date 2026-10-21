from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Outcome(StrEnum):
    TRIAGED = "triaged"
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
    priority: Priority | None = None
    owner: str | None = None
    escalation: str | None = None
    escalate_after_minutes: int | None = None
    notify: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    runbook: str | None = None
