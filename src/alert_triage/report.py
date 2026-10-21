"""Self-contained HTML report: one file, no scripts, no external resources."""

from html import escape
from pathlib import PurePath

from alert_triage.models import Outcome, TriageResult

LEGEND = [
    (
        "P1",
        "Prod, customers affected, outage or errors at least 2x threshold. Pages EOC on-call; "
        "Opsgenie escalates if not acknowledged in 5 min.",
    ),
    (
        "P2",
        "Prod, customers affected. Pages EOC on-call; Opsgenie escalates if not acknowledged "
        "in 15 min.",
    ),
    ("P3", "Prod, internal service. Alert to EOC on-call, no page."),
    ("P4", "Non-prod (staging). Alert to EOC on-call, no page."),
    ("review", "The rules cannot decide. A person looks at it; nothing is guessed."),
    ("rejected", "Broken or incomplete data. Dead letter, plus an Opsgenie alert for EOC triage."),
]

STYLE = """
body { font: 15px/1.5 -apple-system, system-ui, sans-serif; margin: 2rem auto; max-width: 1100px;
       padding: 0 1rem; color: #1f2328; }
h1 { margin-bottom: .2rem; } .sub { color: #59636e; margin-top: 0; }
.cards { display: flex; gap: .8rem; flex-wrap: wrap; margin: 1.2rem 0; }
.card { border: 1px solid #d1d9e0; border-radius: 8px; padding: .6rem 1rem; min-width: 7rem; }
.card b { display: block; font-size: 1.6rem; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: .5rem .6rem; border-bottom: 1px solid #e5e9ed;
         vertical-align: top; }
th { background: #f6f8fa; font-weight: 600; }
td:nth-child(-n+6) { white-space: nowrap; }
td:last-child { min-width: 18rem; }
.tag { display: inline-block; border-radius: 4px; padding: 0 .45rem; font-weight: 600;
       font-size: .85rem; color: #fff; background: #59636e; }
.P1 { background: #cf222e; } .P2 { background: #bc4c00; } .P3 { background: #9a6700; }
.P4 { background: #0969da; } .review { background: #8250df; } .rejected { background: #57606a; }
tr.quiet td { color: #59636e; }
details summary { cursor: pointer; color: #0969da; }
details ol { margin: .4rem 0 .2rem; padding-left: 1.4rem; }
.legend td:first-child { width: 6rem; }
code { background: #f6f8fa; padding: 0 .3rem; border-radius: 4px; }
"""


def render_html(results: list[tuple[str, TriageResult]], counts, needs_review: int) -> str:
    cards = [("alerts", len(results)), ("need a person", needs_review)]
    cards += sorted(counts.items())
    rows = "\n".join(_row(source, r) for source, r in results)
    legend = "\n".join(
        f'<tr><td><span class="tag {key}">{key}</span></td><td>{escape(text)}</td></tr>'
        for key, text in LEGEND
    )
    card_html = "".join(f'<div class="card"><b>{n}</b>{escape(str(k))}</div>' for k, n in cards)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Alert triage report</title>
<style>{STYLE}</style></head><body>
<h1>Alert triage report</h1>
<p class="sub">What the tool decided for each alert, and why. Click a row's details for the
first-response checklist.</p>
<div class="cards">{card_html}</div>
<table>
<tr><th>Priority</th><th>Alert</th><th>Service / env</th><th>Result</th><th>Opsgenie</th>
<th>Page</th><th>Why</th></tr>
{rows}
</table>
<h2>How to read it</h2>
<table class="legend">{legend}</table>
</body></html>
"""


def _tag(r: TriageResult) -> str:
    if r.outcome == Outcome.REJECTED:
        return "rejected"
    if r.outcome == Outcome.MANUAL_REVIEW:
        return "review"
    return r.priority.value if r.priority else "--"


def _row(source: str, r: TriageResult) -> str:
    tag = _tag(r)
    if r.fingerprint:
        name, service, env = r.fingerprint.split("|")
        alert = f"{escape(r.alert_id)}<br><small>{escape(name)}</small>"
        where = f"{escape(service)} / {escape(env)}"
    else:
        alert, where = escape(PurePath(source).name), ""
    quiet = r.outcome in (Outcome.IGNORED, Outcome.DUPLICATE, Outcome.RESOLVED)
    return (
        f'<tr class="{"quiet" if quiet else ""}">'
        f'<td><span class="tag {tag}">{escape(tag)}</span></td>'
        f"<td>{alert}</td><td>{where}</td><td>{escape(r.outcome.value)}</td>"
        f"<td>{escape(r.opsgenie_id or '')}</td><td>{'yes' if r.page else ''}</td>"
        f"<td>{_why(r)}</td></tr>"
    )


def _why(r: TriageResult) -> str:
    parts = []
    if r.description:
        parts.append(f"<b>{escape(r.description)}</b>")
    parts.append("<br>".join(escape(reason) for reason in r.reasons))
    if not r.checklist:
        return "<br>".join(parts)
    extra = []
    if r.impact:
        extra.append(f"<p><b>Impact:</b> {escape(r.impact)}</p>")
    if r.ack_timeout_minutes:
        extra.append(
            f"<p><b>Pages:</b> {escape(r.responder or '')}. Not acknowledged in "
            f"{r.ack_timeout_minutes} min: Opsgenie escalates to "
            f"{escape(r.escalation or '')}.</p>"
        )
    if r.escalation:
        extra.append(f"<p><b>Escalate when the runbook says so:</b> {escape(r.escalation)}</p>")
    if r.runbook:
        extra.append(f"<p><b>Runbook:</b> <code>{escape(r.runbook)}</code></p>")
    steps = "".join(f"<li>{escape(step)}</li>" for step in r.checklist)
    parts.append(
        f"<details><summary>Impact and checklist ({len(r.checklist)} steps)</summary>"
        f"{''.join(extra)}<ol>{steps}</ol></details>"
    )
    return "<br>".join(parts)
