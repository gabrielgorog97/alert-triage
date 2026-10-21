from datetime import datetime

from alert_triage.models import Alert

REQUIRED_TEXT_FIELDS = ("alert_id", "name", "service", "environment", "status")
STATUSES = ("firing", "resolved")


class InvalidAlert(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _is_number(value) -> bool:
    # bool is a subclass of int in Python, but true/false is not a measurement.
    return isinstance(value, int | float) and not isinstance(value, bool)


def parse_alert(data) -> Alert:
    """Turn a decoded JSON payload into an Alert, or raise InvalidAlert listing every problem."""
    if not isinstance(data, dict):
        raise InvalidAlert(["payload must be a JSON object"])

    errors = []
    for name in REQUIRED_TEXT_FIELDS:
        value = data.get(name)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"'{name}' is required and must be a non-empty string")

    status = data.get("status")
    if isinstance(status, str) and status not in STATUSES:
        errors.append(f"'status' must be one of {list(STATUSES)}, got '{status}'")

    started_at = None
    raw_started = data.get("started_at")
    if not isinstance(raw_started, str):
        errors.append("'started_at' is required and must be an ISO 8601 string")
    else:
        try:
            started_at = datetime.fromisoformat(raw_started.replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"'started_at' is not a valid ISO 8601 time: '{raw_started}'")

    for name in ("value", "threshold"):
        if data.get(name) is not None and not _is_number(data[name]):
            errors.append(f"'{name}' must be a number when present")

    if errors:
        raise InvalidAlert(errors)

    return Alert(
        alert_id=data["alert_id"].strip(),
        name=data["name"].strip(),
        service=data["service"].strip(),
        environment=data["environment"].strip(),
        status=status,
        started_at=started_at,
        value=data.get("value"),
        threshold=data.get("threshold"),
    )
