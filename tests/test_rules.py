import pytest

from alert_triage.models import Outcome, Priority
from alert_triage.rules import classify
from alert_triage.validation import parse_alert


def triage(alert_data, catalog, **changes):
    return classify(parse_alert({**alert_data, **changes}), catalog)


def test_error_rate_above_threshold_is_p2(alert_data, catalog):
    result = triage(alert_data, catalog)
    assert result.outcome == Outcome.TRIAGED
    assert result.priority == Priority.P2
    assert result.notify == ["page", "ticket"]
    assert result.escalate_after_minutes == 30
    assert result.owner == "payments-team"


@pytest.mark.parametrize("value", [0.10, 0.25])
def test_error_rate_at_or_above_twice_threshold_is_p1(alert_data, catalog, value):
    result = triage(alert_data, catalog, value=value)
    assert result.priority == Priority.P1
    assert result.escalate_after_minutes == 15
    assert result.escalation == "payments-oncall-lead"


def test_just_below_twice_threshold_stays_p2(alert_data, catalog):
    assert triage(alert_data, catalog, value=0.0999).priority == Priority.P2


@pytest.mark.parametrize("name", ["ServiceDown", "DependencyDown"])
def test_outage_on_customer_facing_prod_is_p1(alert_data, catalog, name):
    result = triage(alert_data, catalog, name=name, value=None, threshold=None)
    assert result.priority == Priority.P1


def test_high_latency_is_p2_even_when_far_above_threshold(alert_data, catalog):
    result = triage(alert_data, catalog, name="HighLatency", value=5.0, threshold=0.5)
    assert result.priority == Priority.P2


def test_internal_prod_service_is_p3_ticket_only(alert_data, catalog):
    result = triage(alert_data, catalog, service="report-builder", name="ServiceDown")
    assert result.priority == Priority.P3
    assert result.notify == ["ticket"]
    assert result.escalate_after_minutes is None
    assert result.escalation is None


def test_staging_is_p4_even_for_an_outage(alert_data, catalog):
    result = triage(alert_data, catalog, environment="staging", name="ServiceDown")
    assert result.priority == Priority.P4
    assert result.notify == ["ticket"]


def test_unknown_service_goes_to_manual_review(alert_data, catalog):
    result = triage(alert_data, catalog, service="mystery-svc")
    assert result.outcome == Outcome.MANUAL_REVIEW
    assert result.priority is None
    assert result.owner == "eoc-triage"
    assert "not in the catalog" in result.reasons[0]


def test_environment_not_in_catalog_goes_to_manual_review(alert_data, catalog):
    result = triage(alert_data, catalog, service="report-builder", environment="staging")
    assert result.outcome == Outcome.MANUAL_REVIEW
    assert "not listed" in result.reasons[0]


def test_unknown_alert_type_goes_to_manual_review(alert_data, catalog):
    result = triage(alert_data, catalog, name="DiskAlmostFull")
    assert result.outcome == Outcome.MANUAL_REVIEW


def test_threshold_alert_without_numbers_goes_to_manual_review(alert_data, catalog):
    result = triage(alert_data, catalog, value=None)
    assert result.outcome == Outcome.MANUAL_REVIEW
    assert "needs both" in result.reasons[0]


def test_zero_threshold_goes_to_manual_review(alert_data, catalog):
    result = triage(alert_data, catalog, threshold=0)
    assert result.outcome == Outcome.MANUAL_REVIEW


@pytest.mark.parametrize(
    ("changes", "impact"),
    [
        (
            {},
            "Customers likely affected: checkout-api is customer-facing in prod "
            "(HighErrorRate: some requests fail).",
        ),
        (
            {"name": "DependencyDown"},
            "Customers likely affected: checkout-api is customer-facing "
            "in prod (DependencyDown: features that rely on the dependency may fail).",
        ),
        (
            {"service": "report-builder"},
            "No direct customer impact expected: report-builder is "
            "internal; check which teams or jobs depend on it.",
        ),
        ({"environment": "staging"}, "No customer impact: non-prod environment 'staging'."),
        (
            {"service": "mystery-svc"},
            "Unknown: the rules cannot decide (see why); assess impact by hand.",
        ),
    ],
)
def test_impact_statement(alert_data, catalog, changes, impact):
    assert triage(alert_data, catalog, **changes).impact == impact


def test_all_manual_review_reasons_are_listed(alert_data, catalog):
    result = triage(alert_data, catalog, service="mystery-svc", name="DiskAlmostFull")
    assert len(result.reasons) == 2
