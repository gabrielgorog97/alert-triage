import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from alert_triage.checklist import build_checklist, load_checklists, rejected_checklist
from alert_triage.models import Outcome, Priority, Service, TriageResult
from alert_triage.outbox import Notifier
from alert_triage.rules import (
    FLAPPING_AFTER_REOPENS,
    MANUAL_REVIEW_QUEUE,
    REOPEN_WINDOW_MINUTES,
    classify,
)
from alert_triage.state import StateStore
from alert_triage.validation import InvalidAlert, parse_alert


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class TriageEngine:
    """Runs one alert through the steps in the README: validate, redelivery, resolved, duplicate,
    classify.

    Notifications are sent before state is saved. If the process dies in between, the retry sends
    them again with the same idempotency keys (ticket ids come from saved state, so they repeat
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
            return self.reject(payload, exc.errors)

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

    def reject(self, payload, errors: list[str]) -> TriageResult:
        """Keep an unusable payload and open a triage ticket: it may still be a real alert."""
        alert_id = payload.get("alert_id") if isinstance(payload, dict) else None
        # Rejected payloads may have no usable id, so they are recognised by their content.
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        result = TriageResult(
            alert_id=str(alert_id),
            outcome=Outcome.REJECTED,
            owner=MANUAL_REVIEW_QUEUE,
            notify=["ticket"],
            reasons=list(errors),
        )

        if digest in self.state.rejected:
            result.ticket_id = self.state.rejected[digest]
            result.notify = []
            result.reasons.append(f"same payload already ticketed as {result.ticket_id}")
            return result

        now = self.clock()
        self.notifier.send(
            "dead_letter",
            f"dead_letter:{digest}",
            {"received_at": now, "errors": errors, "payload": payload},
        )
        ticket_id = self.state.new_ticket_id()
        result.ticket_id = ticket_id
        result.checklist = rejected_checklist(self.checklists, ticket_id)
        self.notifier.send(
            "create_ticket",
            f"create_ticket:rejected:{digest}",
            {
                "ticket_id": ticket_id,
                "created_at": now,
                "outcome": result.outcome.value,
                "owner": result.owner,
                "errors": errors,
                "payload": payload,
                "checklist": result.checklist,
            },
        )
        self.state.rejected[digest] = ticket_id
        self.state.save()
        result.reasons.append(f"opened {ticket_id}")
        return result

    def _open(self, alert) -> TriageResult:
        result = classify(alert, self.catalog)
        result.description = alert.description
        now = self.clock()
        recent = self._recently_closed(alert.fingerprint, now)
        reopens = recent["reopens"] + 1 if recent else 0
        ticket_id = recent["ticket_id"] if recent else self.state.new_ticket_id()
        result.ticket_id = ticket_id
        result.checklist = build_checklist(self.checklists, result, alert, ticket_id)

        if recent:
            if result.outcome == Outcome.TRIAGED:
                result.outcome = Outcome.REOPENED
            flapping = reopens >= FLAPPING_AFTER_REOPENS
            self.notifier.send(
                "reopen_ticket",
                f"reopen_ticket:{ticket_id}:{reopens}",
                {
                    "ticket_id": ticket_id,
                    "reopened_at": now,
                    "alert": alert.alert_id,
                    "reopens": reopens,
                    "flapping": flapping,
                    **_summary(result),
                },
            )
        else:
            self.notifier.send(
                "create_ticket",
                f"create_ticket:{alert.fingerprint}:{ticket_id}",
                {
                    "ticket_id": ticket_id,
                    "created_at": now,
                    "alert": alert.alert_id,
                    **_summary(result),
                },
            )
        if "page" in result.notify:
            self.notifier.send(
                "page",
                f"page:{alert.alert_id}",
                {"ticket_id": ticket_id, "sent_at": now, **_summary(result)},
            )
        self.state.open[alert.fingerprint] = {
            "ticket_id": ticket_id,
            "outcome": result.outcome.value,
            "priority": result.priority.value if result.priority else None,
            "first_alert_id": alert.alert_id,
            "first_seen": now,
            "last_seen": now,
            "count": 1,
            "reopens": reopens,
        }
        if not recent:
            result.reasons.append(f"opened {ticket_id}")
            return result
        result.reasons.append(
            f"reopened {ticket_id}: came back within {REOPEN_WINDOW_MINUTES} min of closing "
            f"(reopen {reopens})"
        )
        if reopens >= FLAPPING_AFTER_REOPENS:
            result.reasons.append(
                f"flapping: reopened {reopens} times; its owner should tune the alert definition"
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
            ticket_id=item["ticket_id"],
            priority=Priority(item["priority"]) if item["priority"] else None,
            reasons=[f"already open as {item['ticket_id']}, seen {item['count']} times"],
        )

        # A duplicate must not hide a problem that got worse, e.g. errors going from 1.5x to 3x.
        fresh = classify(alert, self.catalog)
        if fresh.priority and item["priority"] and fresh.priority < item["priority"]:
            old = item["priority"]
            item["priority"] = fresh.priority.value
            result.priority = fresh.priority
            result.reasons.append(f"priority raised {old} -> {fresh.priority.value}")
            if "page" in fresh.notify:
                self.notifier.send(
                    "page",
                    f"page:{alert.alert_id}",
                    {
                        "ticket_id": item["ticket_id"],
                        "sent_at": item["last_seen"],
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
            "resolve_ticket",
            f"resolve_ticket:{item['ticket_id']}:{item.get('reopens', 0)}",
            {"ticket_id": item["ticket_id"], "resolved_at": now, "seen": item["count"]},
        )
        self.state.recent[alert.fingerprint] = {
            "ticket_id": item["ticket_id"],
            "closed_at": now,
            "reopens": item.get("reopens", 0),
        }
        return TriageResult(
            alert_id=alert.alert_id,
            outcome=Outcome.RESOLVED,
            fingerprint=alert.fingerprint,
            ticket_id=item["ticket_id"],
            reasons=[f"closed {item['ticket_id']} after {item['count']} alerts"],
        )


def _summary(result: TriageResult) -> dict:
    return {
        "outcome": result.outcome.value,
        "priority": result.priority.value if result.priority else None,
        "description": result.description,
        "impact": result.impact,
        "owner": result.owner,
        "escalation": result.escalation,
        "escalate_after_minutes": result.escalate_after_minutes,
        "reasons": result.reasons,
        "runbook": result.runbook,
        "checklist": result.checklist,
    }
