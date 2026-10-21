from datetime import datetime

from alert_triage.models import Alert

REQUIRED_TEXT_FIELDS = ("alert_id", "name", "service", "environment", "status")
STATUSES = ("firing", "resolved")


class InvalidAlert(Exception):
    def __init__(self, errors: list[str], fields: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors
        # Which fields were wrong, without their values: the same broken monitor sends different
        # values every time, but the same fields are broken.
        self.fields = fields


def is_number(value) -> bool:
    # bool is a subclass of int in Python, but true/false is not a measurement.
    return isinstance(value, int | float) and not isinstance(value, bool)


def parse_alert(data) -> Alert:
    """Turn a decoded JSON payload into an Alert, or raise InvalidAlert listing every problem."""
    if not isinstance(data, dict):
        raise InvalidAlert(["payload must be a JSON object"], ["payload"])

    errors, fields = [], []

    def problem(field: str, message: str):
        errors.append(message)
        fields.append(field)

    for name in REQUIRED_TEXT_FIELDS:
        value = data.get(name)
        if not isinstance(value, str) or not value.strip():
            problem(name, f"'{name}' is required and must be a non-empty string")

    status = data.get("status")
    if isinstance(status, str) and status not in STATUSES:
        problem("status", f"'status' must be one of {list(STATUSES)}, got '{status}'")

    started_at = None
    raw_started = data.get("started_at")
    if not isinstance(raw_started, str):
        problem("started_at", "'started_at' is required and must be an ISO 8601 string")
    else:
        try:
            started_at = datetime.fromisoformat(raw_started.replace("Z", "+00:00"))
        except ValueError:
            problem("started_at", f"'started_at' is not a valid ISO 8601 time: '{raw_started}'")

    for name in ("value", "threshold"):
        if data.get(name) is not None and not is_number(data[name]):
            problem(name, f"'{name}' must be a number when present")

    if data.get("description") is not None and not isinstance(data["description"], str):
        problem("description", "'description' must be a string when present")

    if errors:
        raise InvalidAlert(errors, fields)

    return Alert(
        alert_id=data["alert_id"].strip(),
        name=data["name"].strip(),
        service=data["service"].strip(),
        environment=data["environment"].strip(),
        status=status,
        started_at=started_at,
        value=data.get("value"),
        threshold=data.get("threshold"),
        description=data.get("description"),
    )
