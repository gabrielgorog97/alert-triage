"""Triage rules. The README's "Triage rules" section is the documentation for this file."""

from alert_triage.models import Alert, Outcome, Priority, Service, TriageResult

KNOWN_ALERTS = ("HighErrorRate", "HighLatency", "DependencyDown", "ServiceDown")
THRESHOLD_ALERTS = ("HighErrorRate", "HighLatency")
OUTAGE_ALERTS = ("ServiceDown", "DependencyDown")
PROD_ENVIRONMENTS = ("prod",)

# P1/P2 page the EOC on-call. If the page is not acknowledged in time, the paging tool escalates
# to the service's escalation contact. Any other escalation is the on-call person's decision.
EOC_ONCALL = "eoc-oncall"
ACK_TIMEOUT_MINUTES = {Priority.P1: 5, Priority.P2: 15}
PAGED_PRIORITIES = tuple(ACK_TIMEOUT_MINUTES)
MANUAL_REVIEW_QUEUE = "eoc-triage"
REOPEN_WINDOW_MINUTES = 30
FLAPPING_AFTER_REOPENS = 3
CUSTOMER_EFFECT = {
    "HighErrorRate": "some requests fail",
    "HighLatency": "requests are slow",
    "DependencyDown": "features that rely on the dependency may fail",
    "ServiceDown": "the service is unavailable",
}


def classify(alert: Alert, catalog: dict[str, Service]) -> TriageResult:
    """Decide manual review or a priority for a firing alert."""
    service = catalog.get(alert.service)
    problems = _manual_review_reasons(alert, service)
    if problems:
        return TriageResult(
            alert_id=alert.alert_id,
            outcome=Outcome.MANUAL_REVIEW,
            fingerprint=alert.fingerprint,
            impact="Unknown: the rules cannot decide (see why); assess impact by hand.",
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
        impact=_impact(alert, priority),
        owner=service.owner,
        escalation=service.escalation,
        page_to=EOC_ONCALL if priority in PAGED_PRIORITIES else None,
        ack_timeout_minutes=ACK_TIMEOUT_MINUTES.get(priority),
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


def _impact(alert: Alert, priority: Priority) -> str:
    if priority == Priority.P4:
        return f"No customer impact: non-prod environment '{alert.environment}'."
    if priority == Priority.P3:
        return (
            f"No direct customer impact expected: {alert.service} is internal; "
            "check which teams or jobs depend on it."
        )
    return (
        f"Customers likely affected: {alert.service} is customer-facing in prod "
        f"({alert.name}: {CUSTOMER_EFFECT[alert.name]})."
    )


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
