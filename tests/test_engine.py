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


def test_new_alert_opens_ticket_and_pages(engine, outbox, alert_data):
    result = engine.process(alert_data)
    assert result.outcome == Outcome.TRIAGED
    assert outbox.actions() == ["create_ticket", "page"]
    assert outbox.events[0]["ticket_id"] == "T-0001"
    assert outbox.events[0]["checklist"][2].startswith("Acknowledge ticket T-0001")


def test_p3_opens_ticket_without_page(engine, outbox, alert_data):
    engine.process(alert(alert_data, "a-1", service="report-builder", name="ServiceDown"))
    assert outbox.actions() == ["create_ticket"]


def test_repeated_alert_is_a_duplicate_with_no_new_work(engine, outbox, alert_data):
    engine.process(alert(alert_data, "a-1"))
    results = [engine.process(alert(alert_data, f"a-{i}")) for i in range(2, 6)]
    assert all(r.outcome == Outcome.DUPLICATE for r in results)
    assert "seen 5 times" in results[-1].reasons[0]
    assert outbox.actions() == ["create_ticket", "page"]


def test_redelivered_alert_id_changes_nothing(engine, outbox, alert_data):
    engine.process(alert_data)
    result = engine.process(alert_data)
    assert result.outcome == Outcome.IGNORED
    assert engine.state.open["HighErrorRate|checkout-api|prod"]["count"] == 1
    assert len(outbox.events) == 2


def test_problem_back_after_the_reopen_window_opens_a_new_ticket(
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
    tickets = [e["ticket_id"] for e in outbox.events if e["action"] == "create_ticket"]
    assert tickets == ["T-0001", "T-0002"]


def test_problem_back_within_the_window_reopens_the_same_ticket(
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
    assert again.ticket_id == "T-0001"
    assert outbox.actions() == ["create_ticket", "page", "resolve_ticket", "reopen_ticket", "page"]
    assert "reopened T-0001" in again.reasons[-1]


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
    assert {r.ticket_id for r in results} == {"T-0001"}
    assert not any("flapping" in x for x in results[1].reasons)
    assert results[2].reasons[-1].startswith("flapping: reopened 3 times")
    reopen_events = [e for e in outbox.events if e["action"] == "reopen_ticket"]
    assert [e["flapping"] for e in reopen_events] == [False, False, True]
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
    assert outbox.actions() == ["create_ticket", "page", "page"]


def test_better_duplicate_keeps_the_higher_priority(engine, outbox, alert_data):
    engine.process(alert(alert_data, "a-1", value=0.20))
    result = engine.process(alert(alert_data, "a-2", value=0.06))
    assert result.priority == Priority.P1
    assert outbox.actions() == ["create_ticket", "page"]


def test_malformed_alert_is_kept_and_ticketed_for_a_person(engine, outbox):
    result = engine.process({"alert_id": "bad-1", "name": "HighErrorRate"})
    assert result.outcome == Outcome.REJECTED
    assert outbox.actions() == ["dead_letter", "create_ticket"]
    assert outbox.events[0]["payload"]["alert_id"] == "bad-1"
    ticket = outbox.events[1]
    assert ticket["owner"] == "eoc-triage"
    assert ticket["payload"]["alert_id"] == "bad-1"
    assert ticket["checklist"][0].startswith("Do not assume it is harmless")
    assert result.ticket_id == "T-0001"


def test_non_object_payload_goes_to_dead_letter(engine, outbox):
    assert engine.process(["not", "an", "object"]).outcome == Outcome.REJECTED


def test_rejected_payloads_are_told_apart_by_content(engine, outbox):
    first = engine.process({"name": "HighErrorRate"})
    second = engine.process({"name": "HighLatency"})
    repeat = engine.process({"name": "HighErrorRate"})

    assert (first.ticket_id, second.ticket_id) == ("T-0001", "T-0002")
    assert repeat.ticket_id == "T-0001"
    assert "same payload already ticketed as T-0001" in repeat.reasons
    assert outbox.actions() == ["dead_letter", "create_ticket"] * 2


def test_rejected_payload_is_remembered_across_runs(catalog, tmp_path, outbox):
    make_engine(catalog, tmp_path, outbox).process({"name": "HighErrorRate"})
    make_engine(catalog, tmp_path, outbox).process({"name": "HighErrorRate"})
    assert outbox.actions() == ["dead_letter", "create_ticket"]


def test_repeated_ambiguous_alert_creates_one_review_ticket(engine, outbox, alert_data):
    for i in range(3):
        engine.process(alert(alert_data, f"a-{i}", service="mystery-svc"))
    assert outbox.actions() == ["create_ticket"]
    assert outbox.events[0]["owner"] == "eoc-triage"


def test_ticket_carries_description_and_impact(engine, outbox, alert_data):
    engine.process({**alert_data, "description": "7% of checkout requests fail"})
    ticket = outbox.events[0]
    assert ticket["description"] == "7% of checkout requests fail"
    assert ticket["impact"].startswith("Customers likely affected: checkout-api")


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
    assert [json.loads(line)["action"] for line in lines] == ["create_ticket", "page"]
