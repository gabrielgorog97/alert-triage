"""Triage rules. The README's "Triage rules" section is the documentation for this file."""

from alert_triage.models import Alert, Outcome, Priority, Service, TriageResult

KNOWN_ALERTS = ("HighErrorRate", "HighLatency", "DependencyDown", "ServiceDown")
THRESHOLD_ALERTS = ("HighErrorRate", "HighLatency")
OUTAGE_ALERTS = ("ServiceDown", "DependencyDown")
PROD_ENVIRONMENTS = ("prod",)

ESCALATE_AFTER_MINUTES = {Priority.P1: 15, Priority.P2: 30}
PAGED_PRIORITIES = (Priority.P1, Priority.P2)
MANUAL_REVIEW_QUEUE = "eoc-triage"


def classify(alert: Alert, catalog: dict[str, Service]) -> TriageResult:
    """Decide manual review or a priority for a firing alert."""
    service = catalog.get(alert.service)
    problems = _manual_review_reasons(alert, service)
    if problems:
        return TriageResult(
            alert_id=alert.alert_id,
            outcome=Outcome.MANUAL_REVIEW,
            fingerprint=alert.fingerprint,
            owner=MANUAL_REVIEW_QUEUE,
            notify=["ticket"],
            reasons=problems,
            runbook=service.runbook if service else None,
        )

    priority, reason = _priority(alert, service)
    return TriageResult(
        alert_id=alert.alert_id,
        outcome=Outcome.TRIAGED,
        fingerprint=alert.fingerprint,
        priority=priority,
        owner=service.owner,
        escalation=service.escalation if priority in ESCALATE_AFTER_MINUTES else None,
        escalate_after_minutes=ESCALATE_AFTER_MINUTES.get(priority),
        notify=["page", "ticket"] if priority in PAGED_PRIORITIES else ["ticket"],
        reasons=[reason],
        runbook=service.runbook,
    )


def _manual_review_reasons(alert: Alert, service: Service | None) -> list[str]:
    reasons = []
    if service is None:
        reasons.append(f"service '{alert.service}' is not in the catalog")
    elif alert.environment not in service.environments:
        reasons.append(
            f"environment '{alert.environment}' is not listed for '{alert.service}' "
            f"(catalog: {list(service.environments)})"
        )
    if alert.name not in KNOWN_ALERTS:
        reasons.append(f"alert type '{alert.name}' is not a known type")
    if alert.name in THRESHOLD_ALERTS:
        if alert.value is None or alert.threshold is None:
            reasons.append(f"'{alert.name}' needs both 'value' and 'threshold' to judge severity")
        elif alert.threshold <= 0:
            reasons.append(f"threshold must be positive, got {alert.threshold}")
    return reasons


def _priority(alert: Alert, service: Service) -> tuple[Priority, str]:
    if alert.environment not in PROD_ENVIRONMENTS:
        return Priority.P4, f"non-prod environment '{alert.environment}'"
    if not service.customer_facing:
        return Priority.P3, "prod, internal service (not customer-facing)"
    if alert.name in OUTAGE_ALERTS:
        return Priority.P1, f"prod, customer-facing, {alert.name}"
    if alert.name == "HighErrorRate" and alert.value >= 2 * alert.threshold:
        return (
            Priority.P1,
            f"prod, customer-facing, error rate {alert.value} is at least 2x "
            f"threshold {alert.threshold}",
        )
    return Priority.P2, f"prod, customer-facing, {alert.name} above threshold"
