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
        # Recently closed items, so a problem that comes back soon is linked to its last alert.
        self.recent: dict[str, dict] = {}
        # Content hash of each unreadable payload -> the Opsgenie alert opened for it.
        self.rejected: dict[str, str] = {}
        self.next_alert = 1
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            self.processed = set(data["processed"])
            self.open = data["open"]
            self.next_alert = data["next_alert"]
            self.recent = data.get("recent", {})
            self.rejected = data.get("rejected", {})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Starting from empty would open a new Opsgenie alert for every known problem.
            raise StateError(f"state file {self.path} is unreadable: {exc}") from exc

    def new_opsgenie_id(self) -> str:
        opsgenie_id = f"OG-{self.next_alert:04d}"
        self.next_alert += 1
        return opsgenie_id

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "processed": sorted(self.processed),
            "open": self.open,
            "recent": self.recent,
            "rejected": self.rejected,
            "next_alert": self.next_alert,
        }
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".state-")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.path)
