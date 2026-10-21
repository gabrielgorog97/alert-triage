import json
import shutil
from pathlib import Path

import pytest

from alert_triage.cli import EXIT_CONFIG_ERROR, EXIT_NEEDS_REVIEW, EXIT_OK, main

EXAMPLES = Path(__file__).parent.parent / "examples"


def run(tmp_path, *inputs, catalog=EXAMPLES / "services.yaml", extra=()):
    argv = ["run", *map(str, inputs), "--catalog", str(catalog)]
    argv += ["--state", str(tmp_path / "state.json"), "--outbox", str(tmp_path / "outbox")]
    return main([*argv, *extra])


def test_examples_end_to_end(tmp_path, capsys):
    code = run(tmp_path, EXAMPLES / "alerts", extra=["--json"])
    report = json.loads(capsys.readouterr().out)
    by_id = {}
    for r in report["results"]:
        # a-1001 appears twice: first triaged, then its redelivery is ignored.
        by_id.setdefault(r["alert_id"], r)

    assert code == EXIT_NEEDS_REVIEW
    assert by_id["a-1001"]["priority"] == "P2"
    assert by_id["a-2001"]["priority"] == "P2"
    assert by_id["a-3001"]["priority"] == "P1"
    assert by_id["a-1003"]["outcome"] == "duplicate"
    assert by_id["a-1003"]["priority"] == "P1"
    assert by_id["a-5001"]["priority"] == "P3"
    assert by_id["a-6001"]["priority"] == "P4"
    assert by_id["a-7001"]["outcome"] == "manual_review"
    assert by_id["a-1004"]["outcome"] == "resolved"
    assert [r["outcome"] for r in report["results"] if r["alert_id"] == "a-1001"] == [
        "triaged",
        "ignored",
    ]
    assert by_id["a-1005"]["outcome"] == "reopened"
    assert by_id["a-1005"]["opsgenie_id"] == "OG-0009"
    assert "closing OG-0001" in by_id["a-1005"]["reasons"][-1]
    rejected = [r for r in report["results"] if r["outcome"] == "rejected"]
    assert [(r["opsgenie_id"], r["owner"], r["page"]) for r in rejected] == [
        ("OG-0007", "eoc-triage", False),
        ("OG-0008", "eoc-triage", False),
        ("OG-0010", "payments-team", True),
        ("OG-0007", "eoc-triage", False),
    ]
    assert rejected[2]["priority"] == "P1"
    assert report["summary"]["rejected"] == 4
    assert report["summary"]["needs_review"] == 5


def test_second_run_on_same_input_creates_no_new_work(tmp_path, capsys):
    run(tmp_path, EXAMPLES / "alerts")
    capsys.readouterr()
    events = (tmp_path / "outbox" / "events.jsonl").read_text().splitlines()

    run(tmp_path, EXAMPLES / "alerts", extra=["--json"])
    report = json.loads(capsys.readouterr().out)
    accepted = [r for r in report["results"] if r["outcome"] != "rejected"]
    assert all(r["outcome"] == "ignored" for r in accepted)
    rerun_events = (tmp_path / "outbox" / "events.jsonl").read_text().splitlines()
    assert rerun_events == events
    rejected = [r for r in report["results"] if r["outcome"] == "rejected"]
    assert all("same payload already received" in r["reasons"][-1] for r in rejected)


def test_default_output_is_one_line_per_alert(tmp_path, capsys):
    run(tmp_path, EXAMPLES / "alerts")
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == [
        "PRI",
        "ALERT",
        "SERVICE/ENV",
        "TYPE",
        "RESULT",
        "OPSGENIE",
        "PAGE",
        "WHY",
    ]
    assert lines[1].split()[:7] == [
        "P2",
        "a-1001",
        "checkout-api/prod",
        "HighErrorRate",
        "triaged",
        "OG-0001",
        "yes",
    ]
    assert any(line.startswith("--   08-malformed.json") and "(+2 more)" in line for line in lines)
    assert not any("checklist" in line for line in lines[:13])


def test_html_report_is_written_and_escaped(tmp_path, capsys):
    alert = json.loads((EXAMPLES / "alerts" / "01-error-rate.json").read_text())
    alert["description"] = "<script>alert(1)</script>"
    (tmp_path / "a.json").write_text(json.dumps(alert))
    report = tmp_path / "out" / "report.html"

    assert run(tmp_path, tmp_path / "a.json", extra=["--html", str(report)]) == EXIT_OK
    html = report.read_text()
    assert '<span class="tag P2">P2</span>' in html
    assert "Impact and checklist (16 steps)" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert f"HTML report: {report}" in capsys.readouterr().out


def test_clean_batch_exits_zero(tmp_path, capsys):
    assert run(tmp_path, EXAMPLES / "alerts" / "01-error-rate.json", extra=["--details"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "[P2] a-1001  HighErrorRate  checkout-api/prod  -> triaged  OG-0001" in out
    assert "what: 7% of checkout requests" in out
    assert "Needs human review: 0." in out


def test_missing_catalog_is_a_config_error(tmp_path, capsys):
    code = run(tmp_path, EXAMPLES / "alerts", catalog=tmp_path / "missing.yaml")
    assert code == EXIT_CONFIG_ERROR
    assert "cannot read catalog" in capsys.readouterr().err


def test_missing_input_is_a_config_error(tmp_path, capsys):
    assert run(tmp_path, tmp_path / "nope") == EXIT_CONFIG_ERROR


def test_empty_directory_is_a_config_error(tmp_path):
    (tmp_path / "empty").mkdir()
    assert run(tmp_path, tmp_path / "empty") == EXIT_CONFIG_ERROR


def test_corrupt_state_stops_before_any_work(tmp_path):
    (tmp_path / "state.json").write_text("garbage")
    assert run(tmp_path, EXAMPLES / "alerts") == EXIT_CONFIG_ERROR
    assert not (tmp_path / "outbox").exists()


def test_unreadable_file_does_not_stop_the_batch(tmp_path, capsys):
    shutil.copy(EXAMPLES / "alerts" / "09-not-json.json", tmp_path / "a.json")
    shutil.copy(EXAMPLES / "alerts" / "02-latency.json", tmp_path / "b.json")
    code = run(tmp_path, tmp_path / "a.json", tmp_path / "b.json")
    out = capsys.readouterr().out
    assert code == EXIT_NEEDS_REVIEW
    assert "alert file is not valid JSON" in out
    assert "a-2001" in out and "triaged" in out


def test_usage_error_exits_two():
    with pytest.raises(SystemExit) as exc:
        main(["run"])
    assert exc.value.code == 2
