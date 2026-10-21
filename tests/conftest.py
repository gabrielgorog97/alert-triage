import pytest

from alert_triage.models import Service


@pytest.fixture
def catalog():
    return {
        "checkout-api": Service(
            name="checkout-api",
            owner="payments-team",
            escalation="payments-oncall-lead",
            customer_facing=True,
            environments=("prod", "staging"),
            runbook="https://runbooks.example.com/checkout-api",
        ),
        "report-builder": Service(
            name="report-builder",
            owner="data-team",
            escalation="data-oncall-lead",
            customer_facing=False,
            environments=("prod",),
            runbook="https://runbooks.example.com/report-builder",
        ),
    }


@pytest.fixture
def alert_data():
    """A valid firing alert; tests copy it and change one thing."""
    return {
        "alert_id": "a-1001",
        "name": "HighErrorRate",
        "service": "checkout-api",
        "environment": "prod",
        "status": "firing",
        "value": 0.07,
        "threshold": 0.05,
        "started_at": "2026-10-21T08:00:00Z",
    }
