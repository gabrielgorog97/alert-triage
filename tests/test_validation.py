import pytest

from alert_triage.validation import InvalidAlert, parse_alert


def test_valid_alert_is_parsed(alert_data):
    alert = parse_alert(alert_data)
    assert alert.alert_id == "a-1001"
    assert alert.started_at.tzinfo is not None
    assert alert.fingerprint == "HighErrorRate|checkout-api|prod"


def test_value_and_threshold_are_optional(alert_data):
    del alert_data["value"], alert_data["threshold"]
    assert parse_alert(alert_data).value is None


@pytest.mark.parametrize("payload", [None, [], "text", 42])
def test_non_object_payload_is_rejected(payload):
    with pytest.raises(InvalidAlert, match="JSON object"):
        parse_alert(payload)


def test_every_problem_is_reported_at_once(alert_data):
    del alert_data["service"]
    alert_data["status"] = "exploded"
    alert_data["started_at"] = "yesterday"
    with pytest.raises(InvalidAlert) as exc:
        parse_alert(alert_data)
    assert len(exc.value.errors) == 3


@pytest.mark.parametrize("field", ["alert_id", "name", "service", "environment", "status"])
def test_blank_required_field_is_rejected(alert_data, field):
    alert_data[field] = "   "
    with pytest.raises(InvalidAlert, match=field):
        parse_alert(alert_data)


@pytest.mark.parametrize("bad", ["0.12", True, [1]])
def test_value_must_be_a_number(alert_data, bad):
    alert_data["value"] = bad
    with pytest.raises(InvalidAlert, match="'value' must be a number"):
        parse_alert(alert_data)
