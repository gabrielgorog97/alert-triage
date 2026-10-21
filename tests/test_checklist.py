import pytest

from alert_triage.checklist import ChecklistError, build_checklist, load_checklists
from alert_triage.rules import KNOWN_ALERTS, classify
from alert_triage.validation import parse_alert


@pytest.fixture(scope="module")
def templates():
    return load_checklists()


def checklist_for(templates, catalog, alert_data, **changes):
    alert = parse_alert({**alert_data, **changes})
    return build_checklist(templates, classify(alert, catalog), alert, "T-0042")


def test_evidence_comes_before_mitigation(templates, catalog, alert_data):
    steps = checklist_for(templates, catalog, alert_data)
    evidence = next(i for i, s in enumerate(steps) if s.startswith("Check code, deploy"))
    guardrail = next(i for i, s in enumerate(steps) if s.startswith("Only do steps the runbook"))
    rollback = next(i for i, s in enumerate(steps) if "rollback procedure" in s)
    assert steps[0].startswith("Read the alert: priority P2")
    assert steps[1].startswith("Open the runbook")
    assert evidence < guardrail < rollback


def test_placeholders_are_filled(templates, catalog, alert_data):
    text = "\n".join(checklist_for(templates, catalog, alert_data))
    assert "{" not in text
    assert "0.07 vs threshold 0.05" in text
    assert "https://runbooks.example.com/checkout-api" in text


def test_p1_has_escalation_and_incident_steps(templates, catalog, alert_data):
    steps = checklist_for(templates, catalog, alert_data, name="ServiceDown")
    assert steps[3] == (
        "Acknowledge the page within 5 minutes. If nobody acknowledges it, it escalates "
        "automatically to payments-oncall-lead."
    )
    assert any(s.startswith("Open an incident") for s in steps)
    assert steps[-1].startswith("Before closing ticket T-0042")


def test_p3_has_no_page_but_can_still_be_escalated(templates, catalog, alert_data):
    steps = checklist_for(templates, catalog, alert_data, service="report-builder")
    assert not any(s.startswith("Acknowledge the page") for s in steps)
    assert any(s.startswith("If the runbook says to escalate") for s in steps)


def test_escalation_has_no_timer_it_is_the_on_call_decision(templates, catalog, alert_data):
    steps = checklist_for(templates, catalog, alert_data)
    escalation = next(s for s in steps if s.startswith("If the runbook says to escalate"))
    assert "minutes" not in escalation


def test_manual_review_checklist_says_do_not_change_anything(templates, catalog, alert_data):
    steps = checklist_for(templates, catalog, alert_data, service="mystery-svc")
    assert steps[0].startswith("Do not change anything yet")
    assert "'mystery-svc'" in steps[1]


@pytest.mark.parametrize("name", KNOWN_ALERTS)
def test_every_known_alert_type_renders(templates, catalog, alert_data, name):
    assert checklist_for(templates, catalog, alert_data, name=name)


def test_missing_alert_type_in_templates_is_an_error(tmp_path):
    path = tmp_path / "checklists.yaml"
    path.write_text(
        "assess: []\nimpact: {prod: x, non_prod: y}\nevidence_common: []\nalerts: {}\n"
        "guardrail: x\nescalate: {}\nclose: []\nmanual_review: []\nrejected: []\n"
    )
    with pytest.raises(ChecklistError, match="HighErrorRate"):
        load_checklists(path)


def test_impact_without_non_prod_step_is_an_error(tmp_path):
    path = tmp_path / "checklists.yaml"
    path.write_text(
        "assess: []\nimpact: {prod: x}\nevidence_common: []\nalerts: {}\n"
        "guardrail: x\nescalate: {}\nclose: []\nmanual_review: []\nrejected: []\n"
    )
    with pytest.raises(ChecklistError, match="non_prod"):
        load_checklists(path)


def test_prod_asks_about_customers(templates, catalog, alert_data):
    steps = checklist_for(templates, catalog, alert_data)
    assert steps[4].startswith("Check customer impact")


def test_staging_asks_who_is_blocked_not_about_customers(templates, catalog, alert_data):
    steps = checklist_for(templates, catalog, alert_data, environment="staging")
    assert steps[3].startswith("Check who is blocked: is staging needed")
    assert not any("customer impact" in s for s in steps)


def test_escalation_asks_for_the_evidence_already_collected(templates, catalog, alert_data):
    steps = checklist_for(templates, catalog, alert_data)
    escalation = next(s for s in steps if s.startswith("If the runbook says to escalate"))
    assert "what you checked" in escalation


def test_missing_section_is_an_error(tmp_path):
    path = tmp_path / "checklists.yaml"
    path.write_text("assess: []\n")
    with pytest.raises(ChecklistError, match="'impact'"):
        load_checklists(path)
