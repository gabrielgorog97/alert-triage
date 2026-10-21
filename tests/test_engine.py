import json

import pytest

from alert_triage.engine import TriageEngine
from alert_triage.models import Outcome, Priority
from alert_triage.outbox import FileOutbox, MemoryOutbox
from alert_triage.state import StateError, StateStore


@pytest.fixture
def outbox():
    return MemoryOutbox()


@pytest.fixture
def engine(catalog, tmp_path, outbox):
    return make_engine(catalog, tmp_path, outbox)


def make_engine(catalog, tmp_path, outbox, clock=lambda: "2026-10-21T08:00:00"):
    return TriageEngine(catalog, StateStore(tmp_path / "state.json"), outbox, clock=clock)


class Clock:
    """Test clock that only moves when told to."""

    def __init__(self, minute=0):
        self.minute = minute

    def __call__(self):
        return f"2026-10-21T08:{self.minute:02d}:00"


def alert(alert_data, alert_id, **changes):
    return {**alert_data, "alert_id": alert_id, **changes}


def test_new_alert_creates_an_opsgenie_alert_that_pages_eoc(engine, outbox, alert_data):
    result = engine.process(alert_data)
    assert result.outcome == Outcome.TRIAGED
    assert outbox.actions() == ["create_alert"]
    created = outbox.events[0]
    assert created["opsgenie_id"] == "OG-0001"
    assert created["alias"] == "HighErrorRate|checkout-api|prod"
    assert (created["page"], created["responders"]) == (True, ["eoc-oncall"])
    assert created["checklist"][0].startswith("Acknowledge Opsgenie alert OG-0001")


def test_p3_goes_to_eoc_without_a_page(engine, outbox, alert_data):
    engine.process(alert(alert_data, "a-1", service="report-builder", name="ServiceDown"))
    created = outbox.events[0]
    assert (created["page"], created["responders"]) == (False, ["eoc-oncall"])
    assert created["owner"] == "data-team"


def test_repeated_alert_is_a_duplicate_with_no_new_work(engine, outbox, alert_data):
    engine.process(alert(alert_data, "a-1"))
    results = [engine.process(alert(alert_data, f"a-{i}")) for i in range(2, 6)]
    assert all(r.outcome == Outcome.DUPLICATE for r in results)
    assert "seen 5 times" in results[-1].reasons[0]
    assert outbox.actions() == ["create_alert"]


def test_redelivered_alert_id_changes_nothing(engine, outbox, alert_data):
    engine.process(alert_data)
    result = engine.process(alert_data)
    assert result.outcome == Outcome.IGNORED
    assert engine.state.open["HighErrorRate|checkout-api|prod"]["count"] == 1
    assert len(outbox.events) == 1


def test_problem_back_after_the_reopen_window_is_a_new_unlinked_alert(
    catalog, tmp_path, outbox, alert_data
):
    clock = Clock()
    engine = make_engine(catalog, tmp_path, outbox, clock)
    engine.process(alert(alert_data, "a-1"))
    clock.minute = 5
    assert engine.process(alert(alert_data, "a-2", status="resolved")).outcome == Outcome.RESOLVED
    clock.minute = 36
    again = engine.process(alert(alert_data, "a-3"))
    assert again.outcome == Outcome.TRIAGED
    created = [e for e in outbox.events if e["action"] == "create_alert"]
    assert [(e["opsgenie_id"], e["previous_alert"]) for e in created] == [
        ("OG-0001", None),
        ("OG-0002", None),
    ]


def test_problem_back_within_the_window_is_linked_to_the_closed_alert(
    catalog, tmp_path, outbox, alert_data
):
    clock = Clock()
    engine = make_engine(catalog, tmp_path, outbox, clock)
    engine.process(alert(alert_data, "a-1"))
    clock.minute = 5
    engine.process(alert(alert_data, "a-2", status="resolved"))
    clock.minute = 20
    again = engine.process(alert(alert_data, "a-3"))

    assert again.outcome == Outcome.REOPENED
    assert again.opsgenie_id == "OG-0002"
    assert outbox.actions() == ["create_alert", "close_alert", "create_alert"]
    assert outbox.events[-1]["previous_alert"] == "OG-0001"
    assert outbox.events[-1]["page"] is True
    assert "closing OG-0001, linked to it (reopen 1)" in again.reasons[-1]


def test_repeated_reopens_are_marked_flapping(catalog, tmp_path, outbox, alert_data):
    clock = Clock()
    engine = make_engine(catalog, tmp_path, outbox, clock)
    engine.process(alert(alert_data, "a-0"))
    results = []
    for i in range(1, 4):
        clock.minute += 2
        engine.process(alert(alert_data, f"r-{i}", status="resolved"))
        clock.minute += 2
        results.append(engine.process(alert(alert_data, f"f-{i}")))

    assert [r.outcome for r in results] == [Outcome.REOPENED] * 3
    assert not any("flapping" in x for x in results[1].reasons)
    assert results[2].reasons[-1].startswith("flapping: back 3 times")
    created = [e for e in outbox.events if e["action"] == "create_alert"]
    assert [e["previous_alert"] for e in created] == [None, "OG-0001", "OG-0002", "OG-0003"]
    assert [e["tags"] for e in created] == [[], [], [], ["flapping"]]
    assert len(engine.state.open) == 1
    assert len({e["idempotency_key"] for e in outbox.events}) == len(outbox.events)


def test_reopen_survives_a_restart(catalog, tmp_path, outbox, alert_data):
    clock = Clock()
    make_engine(catalog, tmp_path, outbox, clock).process(alert(alert_data, "a-1"))
    clock.minute = 5
    make_engine(catalog, tmp_path, outbox, clock).process(
        alert(alert_data, "a-2", status="resolved")
    )
    clock.minute = 10
    again = make_engine(catalog, tmp_path, outbox, clock).process(alert(alert_data, "a-3"))
    assert again.outcome == Outcome.REOPENED


def test_old_closures_are_forgotten(catalog, tmp_path, outbox, alert_data):
    clock = Clock()
    engine = make_engine(catalog, tmp_path, outbox, clock)
    engine.process(alert(alert_data, "a-1"))
    engine.process(alert(alert_data, "a-2", status="resolved"))
    clock.minute = 45
    engine.process(alert(alert_data, "b-1", service="report-builder"))
    assert engine.state.recent == {}


def test_resolved_with_nothing_open_is_ignored(engine, outbox, alert_data):
    result = engine.process(alert(alert_data, "a-1", status="resolved"))
    assert result.outcome == Outcome.IGNORED
    assert outbox.events == []


def test_worse_duplicate_raises_priority_and_pages_again(engine, outbox, alert_data):
    engine.process(alert(alert_data, "a-1", value=0.07))
    result = engine.process(alert(alert_data, "a-2", value=0.20))
    assert result.outcome == Outcome.DUPLICATE
    assert result.priority == Priority.P1
    assert "raised P2 -> P1" in result.reasons[-1]
    assert outbox.actions() == ["create_alert", "update_priority"]
    assert outbox.events[-1]["opsgenie_id"] == "OG-0001"
    assert outbox.events[-1]["priority"] == "P1"


def test_better_duplicate_keeps_the_higher_priority(engine, outbox, alert_data):
    engine.process(alert(alert_data, "a-1", value=0.20))
    result = engine.process(alert(alert_data, "a-2", value=0.06))
    assert result.priority == Priority.P1
    assert outbox.actions() == ["create_alert"]


def test_malformed_alert_is_kept_and_sent_to_eoc_triage(engine, outbox):
    result = engine.process({"alert_id": "bad-1", "name": "HighErrorRate"})
    assert result.outcome == Outcome.REJECTED
    assert outbox.actions() == ["dead_letter", "create_alert"]
    assert outbox.events[0]["payload"]["alert_id"] == "bad-1"
    created = outbox.events[1]
    assert (created["responders"], created["page"]) == (["eoc-triage"], False)
    assert created["payload"]["alert_id"] == "bad-1"
    assert created["checklist"][1].startswith("Do not assume it is harmless")
    assert result.opsgenie_id == "OG-0001"


def test_non_object_payload_goes_to_dead_letter(engine, outbox):
    assert engine.process(["not", "an", "object"]).outcome == Outcome.REJECTED


def test_rejected_payloads_are_told_apart_by_what_is_broken(engine, outbox):
    first = engine.process({"name": "HighErrorRate"})
    second = engine.process({"name": "HighLatency"})
    repeat = engine.process({"name": "HighErrorRate"})

    assert (first.opsgenie_id, second.opsgenie_id) == ("OG-0001", "OG-0002")
    assert repeat.opsgenie_id == "OG-0001"
    assert "same payload already received; it is in OG-0001" in repeat.reasons
    assert outbox.actions() == ["dead_letter", "create_alert"] * 2


def test_same_broken_monitor_shares_one_alert(engine, outbox):
    broken = {"name": "HighErrorRate", "environment": "prod", "status": "firing"}
    results = [
        engine.process({**broken, "alert_id": f"b-{i}", "started_at": f"{i} min ago"})
        for i in range(3)
    ]
    assert {r.opsgenie_id for r in results} == {"OG-0001"}
    assert "seen 3 times" in results[-1].reasons[-1]
    assert outbox.actions() == ["dead_letter", "create_alert", "dead_letter", "dead_letter"]


def test_broken_alert_that_reads_as_p1_still_pages_eoc(engine, outbox, alert_data):
    result = engine.process({**alert_data, "name": "ServiceDown", "started_at": "5 minutes ago"})
    assert result.outcome == Outcome.REJECTED
    assert (result.priority, result.page) == (Priority.P1, True)
    assert result.reasons[0].startswith("data incomplete, but name, service and environment say P1")
    created = outbox.events[1]
    assert (created["responders"], created["page"]) == (["eoc-oncall"], True)
    assert created["escalation"] == "payments-oncall-lead"
    assert created["checklist"][0].endswith(
        "Do it within 5 minutes: after that Opsgenie escalates it automatically to "
        "payments-oncall-lead."
    )


def test_broken_alert_that_reads_as_p3_does_not_page(engine, outbox, alert_data):
    result = engine.process(
        {**alert_data, "service": "report-builder", "name": "ServiceDown", "started_at": "now"}
    )
    assert (result.priority, result.page, result.responder) == (None, False, "eoc-triage")


def test_broken_resolved_alert_does_not_page(engine, outbox, alert_data):
    payload = {**alert_data, "name": "ServiceDown", "status": "resolved", "started_at": "now"}
    assert engine.process(payload).page is False


def test_rejected_payload_is_remembered_across_runs(catalog, tmp_path, outbox):
    make_engine(catalog, tmp_path, outbox).process({"name": "HighErrorRate"})
    make_engine(catalog, tmp_path, outbox).process({"name": "HighErrorRate"})
    assert outbox.actions() == ["dead_letter", "create_alert"]


def test_repeated_ambiguous_alert_creates_one_review_alert(engine, outbox, alert_data):
    for i in range(3):
        engine.process(alert(alert_data, f"a-{i}", service="mystery-svc"))
    assert outbox.actions() == ["create_alert"]
    assert outbox.events[0]["responders"] == ["eoc-triage"]


def test_opsgenie_alert_carries_description_and_impact(engine, outbox, alert_data):
    engine.process({**alert_data, "description": "7% of checkout requests fail"})
    created = outbox.events[0]
    assert created["description"] == "7% of checkout requests fail"
    assert created["impact"].startswith("Customers likely affected: checkout-api")


def test_state_survives_a_restart(catalog, tmp_path, alert_data):
    first = make_engine(catalog, tmp_path, MemoryOutbox())
    first.process(alert(alert_data, "a-1"))

    outbox = MemoryOutbox()
    second = make_engine(catalog, tmp_path, outbox)
    assert second.process(alert(alert_data, "a-1")).outcome == Outcome.IGNORED
    assert second.process(alert(alert_data, "a-2")).outcome == Outcome.DUPLICATE
    assert outbox.events == []


def test_corrupt_state_file_stops_the_run(tmp_path):
    (tmp_path / "state.json").write_text("{not json")
    with pytest.raises(StateError, match="unreadable"):
        StateStore(tmp_path / "state.json")


def test_file_outbox_writes_one_json_line_per_action(catalog, tmp_path, alert_data):
    engine = make_engine(catalog, tmp_path, FileOutbox(tmp_path / "outbox"))
    engine.process(alert_data)
    lines = (tmp_path / "outbox" / "events.jsonl").read_text().splitlines()
    assert [json.loads(line)["action"] for line in lines] == ["create_alert"]
