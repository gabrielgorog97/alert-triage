import json
from pathlib import Path
from typing import Protocol


class Notifier(Protocol):
    """What the triage engine needs from paging and ticketing. A real integration (PagerDuty,
    Opsgenie, Jira, ServiceNow) implements the same methods; the engine does not change.

    Every call carries an idempotency key, so a receiver can drop a repeated delivery."""

    def send(self, action: str, idempotency_key: str, payload: dict) -> None: ...


class FileOutbox:
    """Appends one JSON line per action to outbox/events.jsonl instead of calling real systems."""

    def __init__(self, directory: Path):
        self.path = Path(directory) / "events.jsonl"

    def send(self, action: str, idempotency_key: str, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        event = {"action": action, "idempotency_key": idempotency_key, **payload}
        with self.path.open("a") as f:
            f.write(json.dumps(event, default=str) + "\n")


class MemoryOutbox:
    """Test double that keeps events in a list."""

    def __init__(self):
        self.events: list[dict] = []

    def send(self, action: str, idempotency_key: str, payload: dict) -> None:
        self.events.append({"action": action, "idempotency_key": idempotency_key, **payload})

    def actions(self) -> list[str]:
        return [e["action"] for e in self.events]
