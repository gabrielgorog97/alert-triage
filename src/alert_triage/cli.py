import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from alert_triage import __version__
from alert_triage.catalog import CatalogError, load_catalog
from alert_triage.checklist import ChecklistError, load_checklists
from alert_triage.engine import TriageEngine
from alert_triage.models import Outcome, TriageResult
from alert_triage.outbox import FileOutbox
from alert_triage.report import render_html
from alert_triage.state import StateError, StateStore

EXIT_OK = 0
EXIT_CONFIG_ERROR = 3
EXIT_NEEDS_REVIEW = 4
NEEDS_HUMAN = (Outcome.MANUAL_REVIEW, Outcome.REJECTED)


class InputError(Exception):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="alert-triage",
        description="Triage synthetic alerts into priorities, owners and checklists.",
        epilog="Exit codes: 0 all handled by rules, 2 usage error, 3 configuration or input "
        "error (nothing processed), 4 finished but some alerts need human review.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="triage alert files")
    run.add_argument("inputs", nargs="+", type=Path, help="alert JSON files or directories")
    run.add_argument("--catalog", type=Path, required=True, help="service catalog YAML")
    run.add_argument("--checklists", type=Path, help="checklist templates (default: built-in)")
    run.add_argument("--state", type=Path, default=Path("state/state.json"))
    run.add_argument("--outbox", type=Path, default=Path("outbox"))
    run.add_argument("--json", action="store_true", help="machine-readable output")
    run.add_argument(
        "--details", action="store_true", help="show impact, escalation and checklist per alert"
    )
    run.add_argument("--html", type=Path, help="also write an HTML report to this file")
    return parser


def collect_files(inputs: list[Path]) -> list[Path]:
    files = []
    for path in inputs:
        if path.is_dir():
            files.extend(sorted(path.glob("*.json")))
        elif path.is_file():
            files.append(path)
        else:
            raise InputError(f"input not found: {path}")
    if not files:
        raise InputError("no .json alert files found in the given inputs")
    return files


def run(args) -> int:
    try:
        catalog = load_catalog(args.catalog)
        checklists = load_checklists(args.checklists)
        state = StateStore(args.state)
        files = collect_files(args.inputs)
    except (CatalogError, ChecklistError, StateError, InputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    engine = TriageEngine(catalog, state, FileOutbox(args.outbox), checklists)
    results: list[tuple[str, TriageResult]] = []
    for path in files:
        try:
            data = json.loads(path.read_text())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            # One unreadable file must not stop the rest of the batch.
            raw = {"source": str(path)}
            results.append((str(path), engine.reject(raw, [f"cannot read alert file: {exc}"])))
            continue
        for payload in data if isinstance(data, list) else [data]:
            results.append((str(path), engine.process(payload)))

    counts = Counter(r.outcome.value for _, r in results)
    needs_review = sum(1 for _, r in results if r.outcome in NEEDS_HUMAN)
    if args.html:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(render_html(results, counts, needs_review))
    if args.json:
        print(json.dumps(_as_json(results, counts, needs_review), indent=2))
    elif args.details:
        print(_as_text(results, counts, needs_review))
    else:
        print(_as_table(results, counts, needs_review))
        if args.html:
            print(f"HTML report: {args.html}")
    return EXIT_NEEDS_REVIEW if needs_review else EXIT_OK


def _as_json(results, counts, needs_review) -> dict:
    return {
        "results": [{"source": source, **asdict(result)} for source, result in results],
        "summary": {"total": len(results), "needs_review": needs_review, **counts},
    }


def _as_table(results, counts, needs_review) -> str:
    header = ("PRI", "ALERT", "SERVICE/ENV", "TYPE", "RESULT", "TICKET", "NOTIFY", "WHY")
    rows = [header]
    for source, r in results:
        if r.outcome == Outcome.REJECTED:
            label, alert, where, name = "--", Path(source).name, "", ""
            why = f"{r.reasons[0]}" + (
                f" (+{len(r.reasons) - 1} more)" if len(r.reasons) > 1 else ""
            )
        else:
            label = r.priority.value if r.priority else "--"
            name, service, env = r.fingerprint.split("|")
            alert, where = r.alert_id, f"{service}/{env}"
            why = "; ".join(x for x in r.reasons if not x.startswith("opened "))
        rows.append(
            (label, alert, where, name, r.outcome.value, r.ticket_id or "", ",".join(r.notify), why)
        )

    widths = [max(len(row[i]) for row in rows) for i in range(len(header) - 1)]
    lines = [
        "  ".join(c.ljust(w) for c, w in zip(row, widths, strict=False)) + "  " + row[-1]
        for row in rows
    ]
    parts = ", ".join(f"{n} {outcome}" for outcome, n in sorted(counts.items()))
    lines += [
        "",
        f"Summary: {len(results)} alerts: {parts}. Needs human review: {needs_review}.",
        "Add --details for impact and checklists, or --html FILE for a browser report.",
    ]
    return "\n".join(lines)


def _as_text(results, counts, needs_review) -> str:
    lines = []
    for source, r in results:
        label = r.priority.value if r.priority else "--"
        name, service, env = (r.fingerprint or "?|?|?").split("|")
        head = f"[{label}] {r.alert_id}  {name}  {service}/{env}  -> {r.outcome.value}"
        if r.ticket_id:
            head += f"  {r.ticket_id}"

        if r.outcome in (Outcome.TRIAGED, Outcome.MANUAL_REVIEW):
            lines.append(head + f"  owner={r.owner}  notify={','.join(r.notify)}")
            if r.description:
                lines.append(f"     what: {r.description}")
            lines.append(f"     impact: {r.impact}")
            lines.append(f"     why: {'; '.join(r.reasons)}")
            if r.escalate_after_minutes:
                lines.append(f"     escalate: {r.escalation} after {r.escalate_after_minutes} min")
            lines.append("     checklist:")
            lines.extend(f"       {i}. {step}" for i, step in enumerate(r.checklist, 1))
        elif r.outcome == Outcome.REJECTED:
            lines.append(f"[--] {source}  -> rejected")
            lines.extend(f"     error: {e}" for e in r.reasons)
        else:
            lines.append(head + f"  ({'; '.join(r.reasons)})")
        lines.append("")

    parts = ", ".join(f"{n} {outcome}" for outcome, n in sorted(counts.items()))
    lines.append(f"Summary: {len(results)} alerts: {parts}. Needs human review: {needs_review}.")
    return "\n".join(lines)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
