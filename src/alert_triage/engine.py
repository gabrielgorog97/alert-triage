import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import PurePath

from alert_triage.checklist import build_checklist, load_checklists, rejected_checklist
from alert_triage.models import Alert, Outcome, Priority, Service, TriageResult
from alert_triage.outbox import Notifier
from alert_triage.rules import (
    FLAPPING_AFTER_REOPENS,
    MANUAL_REVIEW_QUEUE,
    REOPEN_WINDOW_MINUTES,
    classify,
)
from alert_triage.state import StateStore
from alert_triage.validation import InvalidAlert, is_number, parse_alert


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TriageEngine:
    """Runs one alert through the steps in the README: validate, redelivery, resolved, duplicate,
    classify. The output is Opsgenie alerts (simulated through the notifier).

    Notifications are sent before state is saved. If the process dies in between, the retry sends
    them again with the same idempotency keys (Opsgenie ids come from saved state, so they repeat
    too), and the receiver drops the repeat. The other order could lose a page."""

    def __init__(
        self,
        catalog: dict[str, Service],
        state: StateStore,
        notifier: Notifier,
        checklists: dict | None = None,
        clock: Callable[[], str] = _utcnow,
    ):
        self.catalog = catalog
        self.state = state
        self.notifier = notifier
        self.checklists = checklists if checklists is not None else load_checklists()
        self.clock = clock

    def process(self, payload) -> TriageResult:
        try:
            alert = parse_alert(payload)
        except InvalidAlert as exc:
            return self.reject(payload, exc.errors, exc.fields)

        if alert.alert_id in self.state.processed:
            return TriageResult(
                alert_id=alert.alert_id,
                outcome=Outcome.IGNORED,
                fingerprint=alert.fingerprint,
                reasons=["alert_id already processed (redelivery)"],
            )

        if alert.status == "resolved":
            result = self._resolve(alert)
        elif alert.fingerprint in self.state.open:
            result = self._duplicate(alert)
        else:
            result = self._open(alert)
        result.description = alert.description

        self.state.processed.add(alert.alert_id)
        self._forget_old_closures()
        self.state.save()
        return result

    def reject(self, payload, errors: list[str], fields=()) -> TriageResult:
        """Keep an unusable payload in the dead letter and make sure a person sees it.

        Repeats from the same broken monitor share one open Opsgenie alert. If the name, service
        and environment can still be read and would give P1/P2, EOC is paged anyway: a format
        bug must not hide a real outage."""
        alert_id = payload.get("alert_id") if isinstance(payload, dict) else None
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        result = TriageResult(
            alert_id=str(alert_id),
            outcome=Outcome.REJECTED,
            owner=MANUAL_REVIEW_QUEUE,
            responder=MANUAL_REVIEW_QUEUE,
            reasons=list(errors),
        )

        if digest in self.state.dead_letters:
            group = self.state.rejected[self.state.dead_letters[digest]]
            result.opsgenie_id = group["opsgenie_id"]
            result.reasons.append(f"same payload already received; it is in {result.opsgenie_id}")
            return result

        now = self.clock()
        self.notifier.send(
            "dead_letter",
            f"dead_letter:{digest}",
            {"received_at": now, "errors": errors, "payload": payload},
        )
        key = _rejected_key(payload, fields)
        self.state.dead_letters[digest] = key

        group = self.state.rejected.get(key)
        if group:
            group["count"] += 1
            result.opsgenie_id = group["opsgenie_id"]
            result.reasons.append(
                f"same broken alert already open as {group['opsgenie_id']}, "
                f"seen {group['count']} times"
            )
            self.state.save()
            return result

        urgent = self._readable_part(payload)
        if urgent:
            result.priority = urgent.priority
            result.page = True
            result.responder = urgent.responder
            result.owner = urgent.owner
            result.escalation = urgent.escalation
            result.ack_timeout_minutes = urgent.ack_timeout_minutes
            result.runbook = urgent.runbook
            result.impact = urgent.impact
            result.reasons.insert(
                0,
                f"data incomplete, but name, service and environment say {urgent.priority.value}: "
                "paging EOC",
            )
        opsgenie_id = self.state.new_opsgenie_id()
        result.opsgenie_id = opsgenie_id
        result.checklist = rejected_checklist(self.checklists, result, opsgenie_id)
        self.notifier.send(
            "create_alert",
            f"create_alert:{key}:{opsgenie_id}",
            {
                "opsgenie_id": opsgenie_id,
                "alias": key,
                "created_at": now,
                "responders": [result.responder],
                "page": result.page,
                "tags": ["data-incomplete"],
                "errors": errors,
                "payload": payload,
                **_summary(result),
            },
        )
        self.state.rejected[key] = {"opsgenie_id": opsgenie_id, "count": 1}
        self.state.save()
        result.reasons.append(f"opened {opsgenie_id}")
        return result

    def _readable_part(self, payload) -> TriageResult | None:
        """Classify what can still be read; return it only if it would page."""
        if not isinstance(payload, dict) or payload.get("status") == "resolved":
            return None
        texts = [payload.get(name) for name in ("name", "service", "environment")]
        if not all(isinstance(text, str) and text.strip() for text in texts):
            return None
        name, service, environment = (text.strip() for text in texts)
        alert = Alert(
            alert_id=str(payload.get("alert_id")),
            name=name,
            service=service,
            environment=environment,
            status="firing",
            started_at=None,
            value=payload.get("value") if is_number(payload.get("value")) else None,
            threshold=payload.get("threshold") if is_number(payload.get("threshold")) else None,
        )
        result = classify(alert, self.catalog)
        return result if result.page else None

    def _open(self, alert) -> TriageResult:
        result = classify(alert, self.catalog)
        result.description = alert.description
        now = self.clock()
        # Opsgenie cannot reopen a closed alert, so a problem that comes back gets a new alert
        # linked to the previous one.
        recent = self._recently_closed(alert.fingerprint, now)
        reopens = recent["reopens"] + 1 if recent else 0
        flapping = reopens >= FLAPPING_AFTER_REOPENS
        opsgenie_id = self.state.new_opsgenie_id()
        result.opsgenie_id = opsgenie_id
        result.checklist = build_checklist(self.checklists, result, alert, opsgenie_id)
        if recent and result.outcome == Outcome.TRIAGED:
            result.outcome = Outcome.REOPENED

        self.notifier.send(
            "create_alert",
            f"create_alert:{alert.fingerprint}:{opsgenie_id}",
            {
                "opsgenie_id": opsgenie_id,
                "alias": alert.fingerprint,
                "created_at": now,
                "alert": alert.alert_id,
                "responders": [result.responder],
                "page": result.page,
                "previous_alert": recent["opsgenie_id"] if recent else None,
                "reopens": reopens,
                "tags": ["flapping"] if flapping else [],
                **_summary(result),
            },
        )
        self.state.open[alert.fingerprint] = {
            "opsgenie_id": opsgenie_id,
            "outcome": result.outcome.value,
            "priority": result.priority.value if result.priority else None,
            "first_alert_id": alert.alert_id,
            "first_seen": now,
            "last_seen": now,
            "count": 1,
            "reopens": reopens,
        }
        result.reasons.append(f"opened {opsgenie_id}")
        if recent:
            result.reasons.append(
                f"back within {REOPEN_WINDOW_MINUTES} min of closing {recent['opsgenie_id']}, "
                f"linked to it (reopen {reopens})"
            )
        if flapping:
            result.reasons.append(
                f"flapping: back {reopens} times; its owner should tune the alert definition"
            )
        return result

    def _forget_old_closures(self):
        cutoff = datetime.fromisoformat(self.clock()) - timedelta(minutes=REOPEN_WINDOW_MINUTES)
        for fingerprint, recent in list(self.state.recent.items()):
            if datetime.fromisoformat(recent["closed_at"]) < cutoff:
                del self.state.recent[fingerprint]

    def _recently_closed(self, fingerprint: str, now: str) -> dict | None:
        recent = self.state.recent.pop(fingerprint, None)
        if recent is None:
            return None
        closed = datetime.fromisoformat(recent["closed_at"])
        if datetime.fromisoformat(now) - closed > timedelta(minutes=REOPEN_WINDOW_MINUTES):
            return None
        return recent

    def _duplicate(self, alert) -> TriageResult:
        item = self.state.open[alert.fingerprint]
        item["count"] += 1
        item["last_seen"] = self.clock()
        result = TriageResult(
            alert_id=alert.alert_id,
            outcome=Outcome.DUPLICATE,
            fingerprint=alert.fingerprint,
            opsgenie_id=item["opsgenie_id"],
            priority=Priority(item["priority"]) if item["priority"] else None,
            reasons=[f"already open as {item['opsgenie_id']}, seen {item['count']} times"],
        )

        # A duplicate must not hide a problem that got worse, e.g. errors going from 1.5x to 3x.
        fresh = classify(alert, self.catalog)
        if fresh.priority and item["priority"] and fresh.priority < item["priority"]:
            old = item["priority"]
            item["priority"] = fresh.priority.value
            result.priority = fresh.priority
            result.page = fresh.page
            result.reasons.append(f"priority raised {old} -> {fresh.priority.value}")
            self.notifier.send(
                "update_priority",
                f"update_priority:{alert.alert_id}",
                {
                    "opsgenie_id": item["opsgenie_id"],
                    "updated_at": item["last_seen"],
                    "page": fresh.page,
                    **_summary(fresh),
                },
            )
        return result

    def _resolve(self, alert) -> TriageResult:
        item = self.state.open.pop(alert.fingerprint, None)
        if item is None:
            return TriageResult(
                alert_id=alert.alert_id,
                outcome=Outcome.IGNORED,
                fingerprint=alert.fingerprint,
                reasons=["resolved, but nothing was open for this fingerprint"],
            )
        now = self.clock()
        self.notifier.send(
            "close_alert",
            f"close_alert:{item['opsgenie_id']}",
            {
                "opsgenie_id": item["opsgenie_id"],
                "closed_at": now,
                "seen": item["count"],
                "note": f"closed by the monitor's resolved event after {item['count']} alerts",
            },
        )
        self.state.recent[alert.fingerprint] = {
            "opsgenie_id": item["opsgenie_id"],
            "closed_at": now,
            "reopens": item.get("reopens", 0),
        }
        return TriageResult(
            alert_id=alert.alert_id,
            outcome=Outcome.RESOLVED,
            fingerprint=alert.fingerprint,
            opsgenie_id=item["opsgenie_id"],
            reasons=[f"closed {item['opsgenie_id']} after {item['count']} alerts"],
        )


def _rejected_key(payload, fields) -> str:
    """What identifies a broken monitor: the parts that can be read, and which fields are wrong."""
    if isinstance(payload, dict) and "source" in payload:
        parts = [PurePath(str(payload["source"])).name]
    elif isinstance(payload, dict):
        values = [payload.get(name) for name in ("name", "service", "environment")]
        parts = [v.strip() if isinstance(v, str) and v.strip() else "?" for v in values]
    else:
        parts = ["?"]
    return "rejected|" + "|".join(parts) + "|" + ",".join(sorted(set(fields)))


def _summary(result: TriageResult) -> dict:
    return {
        "outcome": result.outcome.value,
        "priority": result.priority.value if result.priority else None,
        "description": result.description,
        "impact": result.impact,
        "owner": result.owner,
        "escalation": result.escalation,
        "responder": result.responder,
        "ack_timeout_minutes": result.ack_timeout_minutes,
        "reasons": result.reasons,
        "runbook": result.runbook,
        "checklist": result.checklist,
    }
