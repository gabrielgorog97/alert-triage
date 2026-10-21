from importlib import resources
from pathlib import Path

import yaml

from alert_triage.models import Alert, Outcome, Priority, TriageResult
from alert_triage.rules import KNOWN_ALERTS, PROD_ENVIRONMENTS


class ChecklistError(Exception):
    pass


def load_checklists(path: Path | None = None) -> dict:
    """Load checklist templates; the packaged file is used unless a path is given."""
    try:
        if path is None:
            text = resources.files("alert_triage").joinpath("data/checklists.yaml").read_text()
        else:
            text = Path(path).read_text()
        data = yaml.safe_load(text)
    except (OSError, yaml.YAMLError) as exc:
        raise ChecklistError(f"cannot load checklists: {exc}") from exc

    required = ("assess", "impact", "evidence_common", "alerts", "guardrail", "escalate", "close")
    for key in (*required, "manual_review"):
        if not isinstance(data, dict) or key not in data:
            raise ChecklistError(f"checklists are missing '{key}'")
    for key in ("prod", "non_prod"):
        if not isinstance(data["impact"], dict) or not data["impact"].get(key):
            raise ChecklistError(f"checklist 'impact' needs a '{key}' step")
    for name in KNOWN_ALERTS:
        steps = data["alerts"].get(name)
        if not steps or not steps.get("evidence") or not steps.get("mitigate"):
            raise ChecklistError(f"checklist for '{name}' needs 'evidence' and 'mitigate' steps")
    return data


def build_checklist(templates: dict, result: TriageResult, alert: Alert, ticket_id: str) -> list:
    values = {
        "ticket_id": ticket_id,
        "priority": result.priority.value if result.priority else "unprioritized",
        "service": alert.service,
        "environment": alert.environment,
        "value": alert.value,
        "threshold": alert.threshold,
        "started_at": alert.started_at.isoformat(),
        "runbook": result.runbook or "no runbook on file",
        "escalation": result.escalation,
        "escalate_after_minutes": result.escalate_after_minutes,
    }

    if result.outcome == Outcome.MANUAL_REVIEW:
        steps = list(templates["manual_review"])
    else:
        per_alert = templates["alerts"][alert.name]
        impact = "prod" if alert.environment in PROD_ENVIRONMENTS else "non_prod"
        steps = [
            *templates["assess"],
            templates["impact"][impact],
            *templates["evidence_common"],
            *per_alert["evidence"],
            templates["guardrail"],
            *per_alert["mitigate"],
        ]
        if result.escalate_after_minutes:
            steps.append(templates["escalate"]["paged"])
        if result.priority == Priority.P1:
            steps.append(templates["escalate"]["p1"])
        steps.extend(templates["close"])

    return [step.format(**values) for step in steps]
