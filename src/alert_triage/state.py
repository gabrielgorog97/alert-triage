import json
import os
import tempfile
from pathlib import Path


class StateError(Exception):
    pass


class StateStore:
    """Remembers processed alert ids and open items between runs, in one JSON file.

    Writes go to a temporary file that is then renamed over the old one. A crash mid-write leaves
    the previous state intact instead of a half-written file.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.processed: set[str] = set()
        self.open: dict[str, dict] = {}
        self.next_ticket = 1
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            self.processed = set(data["processed"])
            self.open = data["open"]
            self.next_ticket = data["next_ticket"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Starting from empty would reopen every known problem as a new ticket.
            raise StateError(f"state file {self.path} is unreadable: {exc}") from exc

    def new_ticket_id(self) -> str:
        ticket_id = f"T-{self.next_ticket:04d}"
        self.next_ticket += 1
        return ticket_id

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "processed": sorted(self.processed),
            "open": self.open,
            "next_ticket": self.next_ticket,
        }
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)
