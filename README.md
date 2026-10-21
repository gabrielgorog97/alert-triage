# alert-triage

A small CLI that reads synthetic alerts and a service catalog, then produces a triage result and a
first-response checklist for the on-call engineer. Take-home exercise, Option 2 (Alert Triage and
Runbook Assistant).

- [Problem interpretation](#problem-interpretation)
- [Quick start](#quick-start)
- [Usage](#usage)
- [Examples and expected results](#examples-and-expected-results)
- [Design](#design)
- [Tests](#tests)
- [Connecting to real systems](#connecting-to-real-systems)
- [Assumptions](#assumptions)
- [Known limitations](#known-limitations)
- [Time spent and next steps](#time-spent-and-next-steps)
- AI-assisted process: see [AI_PROCESS.md](AI_PROCESS.md)

## Problem interpretation

An alert on its own says *what* crossed a line. The on-call engineer also needs to know how urgent
it is, whether customers feel it, whose service it is, who to escalate to, and what to do first
without making things worse. This tool adds that context from a service catalog, using rules that
are written down and testable, and it:

- never guesses: anything the rules cannot decide goes to a person (manual review);
- never creates unbounded work: repeats of the same problem update one open item;
- never touches production: its only actions are simulated tickets and pages in a local file;
- is safe to re-run on the same input.

## Quick start

Requires Python 3.11+ and `make`.

```bash
make install    # creates .venv and installs the package with dev tools
make demo       # runs the CLI on examples/: short table + demo-run/report.html
open demo-run/report.html   # the same results in the browser, with checklists
make ci         # lint + tests + build wheel (what CI runs)
```

| Target | What it does |
|---|---|
| `make lint` | `ruff check` and `ruff format --check` |
| `make test` | `pytest` |
| `make build` | wheel and sdist in `dist/` (the runnable artifact) |
| `make demo` | run on the examples: one line per alert, plus `demo-run/report.html` |
| `make demo-details` / `make demo-json` | same, with full checklists in the terminal, or JSON |
| `make docker` | container image `alert-triage:local` (non-root) |
| `make clean` | remove venv, build output and demo state |

Dependency versions are pinned in `pyproject.toml`. The same `make ci` runs in
`.github/workflows/ci.yml`.

## Usage

```bash
alert-triage run INPUTS... --catalog services.yaml [--state state/state.json] \
    [--outbox outbox] [--checklists my-checklists.yaml] [--details | --json] [--html FILE]
```

- `INPUTS` are alert JSON files or directories (all `*.json`, in name order). A file may hold one
  alert or a list of alerts.
- `--state` keeps memory between runs (open items, processed ids). Default `state/state.json`.
- `--outbox` is where simulated tickets, pages and rejected payloads are written
  (`events.jsonl`). Default `outbox/`.
- `--checklists` replaces the built-in checklist templates with your own file.
- Default output is one line per alert. `--details` adds impact, escalation and the checklist
  for each alert; `--json` prints machine-readable output instead.
- `--html FILE` also writes a single-file HTML report (no scripts, no external resources) with the
  same results, colour-coded by priority, with each checklist behind a click.

Exit codes, so a script or pipeline can act on the result:

| Code | Meaning |
|---|---|
| 0 | every alert was handled by the rules |
| 2 | wrong command-line usage |
| 3 | configuration or input problem (missing catalog, corrupt state, no input files); nothing was processed |
| 4 | finished, but at least one alert needs a person (manual review or rejected) |

With Docker:

```bash
make docker
docker run --rm -v "$PWD/examples:/work/examples:ro" alert-triage:local \
    run examples/alerts --catalog examples/services.yaml --state /tmp/state.json --outbox /tmp/outbox
```

## Examples and expected results

`examples/services.yaml` defines three made-up services: `checkout-api` and `search-api`
(customer-facing, prod and staging) and `report-builder` (internal, prod only).
`examples/alerts/` has ten files processed in order with one shared state:

| File | Alert | Result | Rule that produced it |
|---|---|---|---|
| `01-error-rate` | HighErrorRate, checkout-api prod, 0.07 vs 0.05 | **P2**, page + ticket T-0001, escalate after 30 min | prod and customer-facing; 1.4x threshold is below the 2x line for P1 |
| `02-latency` | HighLatency, search-api prod, 1.8s vs 0.5s | **P2**, page + ticket T-0002 | prod and customer-facing; latency alone is never P1 (slow is not down) |
| `03-dependency-down` | DependencyDown, checkout-api prod | **P1**, page + ticket T-0003, escalate after 15 min, open incident | prod, customer-facing, outage-type alert |
| `04-repeats` (3 alerts) | `a-1001` again | **ignored** | same `alert_id` already processed: a redelivery |
| | `a-1002`, 0.08 | **duplicate** of T-0001, no new work | same name + service + environment still open |
| | `a-1003`, 0.15 | **duplicate**, priority raised P2 to P1, new page | 3x threshold is P1; a worse repeat must not stay hidden |
| `05-internal` | ServiceDown, report-builder prod | **P3**, ticket only | prod but internal; no customer impact expected |
| `06-staging` | ServiceDown, search-api staging | **P4**, ticket only | non-prod, even for an outage |
| `07-unknown-service` | HighErrorRate, loyalty-points prod | **manual_review**, ticket to `eoc-triage` | service not in the catalog: no owner, no runbook, so no guessing |
| `08-malformed` | missing service, bad time, text value | **rejected**, dead letter | validation: all three errors reported at once |
| `09-not-json` | truncated file | **rejected**, dead letter | file cannot be parsed; the rest of the batch continues |
| `10-resolved` | resolved, checkout-api prod | **resolved**, T-0001 closed | closes the open item with the same fingerprint |

Exit code is 4, because three alerts need a person. Running `make demo`'s command a second time
on the same state produces no new tickets or pages: every alert is a redelivery.

Default output, one line per alert:

```text
PRI  ALERT              SERVICE/ENV          TYPE            RESULT         TICKET  NOTIFY       WHY
P2   a-1001             checkout-api/prod    HighErrorRate   triaged        T-0001  page,ticket  prod, customer-facing, HighErrorRate above threshold
P2   a-2001             search-api/prod      HighLatency     triaged        T-0002  page,ticket  prod, customer-facing, HighLatency above threshold
P1   a-3001             checkout-api/prod    DependencyDown  triaged        T-0003  page,ticket  prod, customer-facing, DependencyDown
--   a-1001             checkout-api/prod    HighErrorRate   ignored                             alert_id already processed (redelivery)
P2   a-1002             checkout-api/prod    HighErrorRate   duplicate      T-0001               already open as T-0001, seen 2 times
P1   a-1003             checkout-api/prod    HighErrorRate   duplicate      T-0001               already open as T-0001, seen 3 times; priority raised P2 -> P1
P3   a-5001             report-builder/prod  ServiceDown     triaged        T-0004  ticket       prod, internal service (not customer-facing)
P4   a-6001             search-api/staging   ServiceDown     triaged        T-0005  ticket       non-prod environment 'staging'
--   a-7001             loyalty-points/prod  HighErrorRate   manual_review  T-0006  ticket       service 'loyalty-points' is not in the catalog
--   08-malformed.json                                       rejected                            'service' is required and must be a non-empty string (+2 more)
--   09-not-json.json                                        rejected                            cannot read alert file: Expecting ',' delimiter: line 2 column 1 (char 70)
--   a-1004             checkout-api/prod    HighErrorRate   resolved       T-0001               closed T-0001 after 3 alerts

Summary: 12 alerts: 2 duplicate, 1 ignored, 1 manual_review, 2 rejected, 1 resolved, 5 triaged. Needs human review: 3.
```

With `--details`, each triaged or manual-review alert also shows impact, escalation and its
checklist:

```text
[P2] a-1001  HighErrorRate  checkout-api/prod  -> triaged  T-0001  owner=payments-team  notify=page,ticket
     what: 7% of checkout requests return 5xx over the last 5 minutes
     impact: Customers likely affected: checkout-api is customer-facing in prod (HighErrorRate: some requests fail).
     why: prod, customer-facing, HighErrorRate above threshold; opened T-0001
     escalate: payments-oncall-lead after 30 min
     checklist:
       1. Read the alert: priority P2, what happened, service checkout-api, environment prod.
       2. Open the runbook before doing anything else: https://runbooks.example.com/checkout-api
       3. Acknowledge ticket T-0001 so others know it is being handled.
       4. Check customer impact: is checkout-api in prod failing for users right now?
       5. Save the relevant Grafana dashboard panels (traffic, errors, latency) for the time around 2026-10-21T08:00:00+00:00.
       6. Check the Kubernetes state of checkout-api: pod status, restarts, recent events.
       7. Collect logs from checkout-api for the time around 2026-10-21T08:00:00+00:00; put a sample in the ticket.
       8. Test checkout-api yourself as a customer would (call the affected endpoint) and record the result.
       9. Check code, deploy and config changes to checkout-api in the hours before 2026-10-21T08:00:00+00:00.
       10. Check whether planned maintenance or a change window covers checkout-api or its dependencies.
       11. Note the error rate (0.07 vs threshold 0.05) and group errors by endpoint and status code.
       12. Check the dependencies listed in the runbook for errors at the same time.
       13. Only do steps the runbook gives to the on-call / EOC role. If a step belongs to another team (for example engineers restart the service), escalate to them instead of doing it yourself.
       14. If a change lines up with the start time, follow the runbook's rollback procedure.
       15. If not mitigated within 30 minutes, escalate to payments-oncall-lead. Include what you checked, the evidence collected, and what you already tried.
       16. Before closing ticket T-0001, write down what was checked, what happened, and how it was resolved. Do not close it without this.

[--] a-7001  HighErrorRate  loyalty-points/prod  -> manual_review  T-0006  owner=eoc-triage  notify=ticket
     what: errors on a service nobody added to the catalog
     impact: Unknown: the rules cannot decide (see why); assess impact by hand.
     why: service 'loyalty-points' is not in the catalog; opened T-0006
     checklist:
       1. Do not change anything yet. Confirm the alert is real by checking the monitor that sent it.
       2. Find the owner of 'loyalty-points': search the service inventory, ask in the EOC channel.
       3. Decide the priority by hand and write the reason in ticket T-0006.
       4. When handing over, include what you checked and why the alert was unclear.
       5. Remove the ambiguity for next time: add the service, environment or alert type to the catalog or fix the alert definition.
```

`--json` prints the same results as objects (`priority`, `impact`, `owner`, `escalation`,
`escalate_after_minutes`, `notify`, `reasons`, `checklist`, `ticket_id`, ...) plus a `summary`
with counts. Each line in `outbox/events.jsonl` is one simulated action with its idempotency key,
for example:

```json
{"action": "page", "idempotency_key": "page:a-1001", "ticket_id": "T-0001", "priority": "P2", "owner": "payments-team", "impact": "Customers likely affected: ...", "checklist": ["..."]}
```

## Design

### Inputs

**Alert** (JSON), as a monitoring system would send it:

```json
{
  "alert_id": "a-1001",
  "name": "HighErrorRate",
  "service": "checkout-api",
  "environment": "prod",
  "status": "firing",
  "value": 0.07,
  "threshold": 0.05,
  "started_at": "2026-10-21T08:00:00Z",
  "description": "7% of checkout requests return 5xx over the last 5 minutes"
}
```

`value`, `threshold` and `description` are optional; the rest is required.

**Service catalog** (YAML), maintained by people, with what the alert does not know:

```yaml
checkout-api:
  owner: payments-team
  escalation: payments-oncall-lead
  customer_facing: true
  environments: [prod, staging]
  runbook: https://runbooks.example.com/checkout-api
```

### Outcomes

Every alert ends in exactly one of these:

| Outcome | Meaning | Work created |
|---|---|---|
| `triaged` | priority, owner and checklist decided | ticket, plus a page for P1/P2 |
| `duplicate` | same alert already open | none, the open item's counter goes up (or a page if priority rises) |
| `manual_review` | readable, but the rules cannot decide | ticket to the EOC triage queue |
| `rejected` | cannot be read or validated | stored in the dead letter |
| `resolved` | closes the open item | resolve note on the existing ticket |
| `ignored` | redelivered `alert_id`, or resolved with nothing open | none |

`manual_review` and `rejected` are different on purpose: "I don't know what this means" is a
decision for a person, "I can't parse this" is broken data.

### Triage rules

Evaluated in this order; the first rule that applies wins. Code: `src/alert_triage/rules.py` and
`engine.py`.

1. **Validation.** `alert_id`, `name`, `service`, `environment`, `status` (`firing` or `resolved`)
   and `started_at` (ISO 8601) are required and must have the right types. Otherwise: `rejected`.
2. **Redelivery.** An `alert_id` that was already processed is ignored, so a retried delivery
   changes nothing.
3. **Resolved.** `status: resolved` closes the open item with the same fingerprint, if any.
4. **Duplicate.** Fingerprint = `name + service + environment`. If an item with that fingerprint is
   open, it is a `duplicate` until a `resolved` alert closes it. A duplicate never opens a new
   ticket, but if it would get a higher priority than the open item (for example errors grew from
   1.5x to 3x threshold), the item's priority is raised and the new priority pages if it should.
5. **Manual review** when any of these is true:
   - the service is not in the catalog
   - the environment is not one the catalog lists for that service
   - the alert name is not a known type
   - a threshold alert (`HighErrorRate`, `HighLatency`) has no `value` or `threshold`, or the
     threshold is 0 or less
6. **Priority:**

| Priority | When | Notify | Escalate if not mitigated |
|---|---|---|---|
| P1 | prod, customer-facing, and `ServiceDown`, `DependencyDown`, or error rate at least 2x threshold | page + ticket | after 15 min, plus open an incident |
| P2 | prod, customer-facing, any other known alert | page + ticket | after 30 min |
| P3 | prod, internal service (not customer-facing) | ticket | no automatic escalation |
| P4 | any non-prod environment (staging) | ticket | no automatic escalation |

Known alert types: `HighErrorRate`, `HighLatency`, `DependencyDown`, `ServiceDown`.

**Customer impact** is stated for every alert: "Customers likely affected" for P1/P2 (with what
customers experience for that alert type), "No direct customer impact expected" for P3, "No
customer impact" for P4, and "Unknown" for manual review.

### Checklists

Templates live in `src/alert_triage/data/checklists.yaml`, not in code, so the people who run the
shift can change the wording without a code change (or pass their own file with `--checklists`).
The order is deliberate:

1. Read the alert and open the runbook before anything else.
2. Check impact (customers in prod; "who is blocked" in non-prod).
3. Collect evidence for every alert type: dashboards, Kubernetes state, logs, test as a customer,
   changes in the hours before the alert started, planned maintenance. Then the evidence specific
   to the alert type.
4. Guardrail: only steps the runbook gives to the on-call role; other teams' steps are escalated.
5. Mitigation from the runbook.
6. Escalation (P1/P2) with the evidence already collected; P1 also opens an incident.
7. Do not close the ticket without writing what was checked, what happened and how it was fixed.

A template file that is missing a section or an alert type stops the run (exit 3), instead of
producing an incomplete checklist.

### State and notifications

- `state/state.json` remembers processed alert ids and open items between runs. It is written to a
  temporary file and renamed, so a crash mid-write keeps the previous state. An unreadable state
  file stops the run, because starting empty would reopen every known problem as a new ticket.
- Tickets, pages and rejected payloads go through a `Notifier` interface (`outbox.py`).
  `FileOutbox` appends them to `outbox/events.jsonl`; tests use `MemoryOutbox`. A real paging or
  ticketing adapter would implement the same single method.
- Every notification has an idempotency key: `create_ticket:<fingerprint>:<ticket>`,
  `page:<alert_id>`, `resolve_ticket:<ticket>`, and `dead_letter:<hash of the payload>` (rejected
  payloads may have no usable id). Notifications are sent before state is saved, so a crash in
  between can repeat a notification with the same key, but never lose a page.

### Code layout

| Module | Responsibility |
|---|---|
| `validation.py` | turn raw JSON into an `Alert`, reporting every error at once |
| `catalog.py` | load and validate the service catalog |
| `rules.py` | manual review or priority, impact, owner, escalation |
| `checklist.py` | load templates, build the ordered checklist |
| `state.py` | persistent state with atomic writes |
| `outbox.py` | `Notifier` interface and the file / memory adapters |
| `engine.py` | the rule order above: redelivery, resolve, duplicate, open |
| `cli.py` | argument parsing, batch processing, table / details / JSON output, exit codes |
| `report.py` | the single-file HTML report |

## Tests

```bash
make test
```

84 tests, no network, each with a temporary state and outbox:

| Area | File | Examples |
|---|---|---|
| Normal alerts | `test_rules.py`, `test_engine.py` | every priority, owner, escalation, impact statement |
| Boundaries | `test_rules.py` | exactly 2x threshold is P1, just below stays P2, threshold 0 |
| Malformed | `test_validation.py`, `test_engine.py`, `test_cli.py` | missing fields, wrong types, booleans as numbers, bad time, not JSON, not an object |
| Duplicate | `test_engine.py`, `test_cli.py` | repeats, redelivery, priority raise, re-running the whole batch |
| Ambiguous | `test_rules.py`, `test_engine.py` | unknown service, environment, alert type, missing numbers; repeated ambiguous alert gives one ticket |
| Failure paths | `test_catalog.py`, `test_checklist.py`, `test_engine.py`, `test_cli.py` | broken catalog, broken templates, corrupt state, missing input, exit codes |
| Checklists | `test_checklist.py` | evidence before mitigation, placeholders filled, non-prod impact question |
| Output | `test_cli.py` | one-line table, HTML report written and HTML-escaped |

## Connecting to real systems

How this design would be connected to monitoring, paging, ticketing and chat, while staying
idempotent, auditable and safe to retry. None of this is implemented.

**Monitoring in.** A small HTTP receiver (for example for Prometheus Alertmanager webhooks)
converts each alert into the input format and calls `TriageEngine.process`. Alertmanager has no
per-delivery id, so `alert_id` would be derived from the alert's fingerprint and `startsAt`, and
`status: resolved` maps directly. The receiver answers 2xx only after the state is saved; if it
fails, the sender retries and the redelivery rule makes the retry harmless.

**State.** The JSON file becomes a database table with unique constraints on `alert_id` and on the
open fingerprint, and each alert is handled in one transaction. That gives correct behaviour when
several receivers run in parallel, which the file cannot.

**Notifications out (outbox pattern).** Instead of calling PagerDuty or Jira inside the alert
transaction, the engine writes the intended action with its idempotency key into an outbox table
in the same transaction. A separate worker delivers them with retries and backoff, and moves
actions that keep failing to a dead-letter queue that someone is alerted on. An outage of the
ticketing system then delays tickets but does not lose them or block triage.

- **Paging:** PagerDuty's Events API accepts a `dedup_key`; the idempotency key (or the
  fingerprint) is passed there, so a repeated send does not page twice.
- **Ticketing:** Jira has no built-in idempotency key, so the adapter stores key-to-ticket mappings
  and checks them (or searches for a label with the key) before creating.
- **Chat:** a message per P1/P2 to the owning team's channel, with the message id stored so updates
  edit the same message. A repeated chat message is the least harmful failure, so it is accepted.

**Audit.** Every decision is already a record: input payload, outcome, reasons, rule inputs, ticket
id, timestamps. In production that log is append-only, kept for the incident review, and each
record also stores the version of the rules, catalog and checklists (git commit) that produced it,
so "why did this page at 3 a.m.?" has an exact answer.

**Safe to operate.**

- The tool only creates tickets and pages; it never runs remediation. Changes stay with people and
  their runbooks.
- Catalog, rules and checklists live in git and change through review, like code.
- It fails closed: a broken catalog or state stops processing and is reported, instead of silently
  sending everything to the wrong place.
- Rollout in shadow mode first (decide and log, but do not page) next to the current routing, and
  compare decisions before switching.
- The tool itself needs monitoring: alerts processed, manual-review rate, dead-letter count,
  delivery failures and age of the oldest undelivered action.

## Assumptions

- Manual-review items go to a single EOC triage queue (`eoc-triage`), since the owner may be
  unknown.
- A threshold alert without numbers is incomplete data (manual review), not malformed input,
  because the rest of the alert is usable.
- Only `prod` is production; every other environment listed in the catalog is non-prod.
- An alert's `name` is its type; the same type on the same service and environment is the same
  problem until it resolves.
- Ticket ids (`T-0001`) are local and only stand in for real ticket numbers.

## Known limitations

- **Single process.** The state file has no locking; two runs at the same time can overwrite each
  other. Fine for a CLI, not for a service.
- **Lost or out-of-order resolve.** An item stays open until a `resolved` alert arrives. If it is
  lost, the item never closes and new alerts for it only count as duplicates. A `resolved` that
  arrives before its `firing` alert is ignored, and the later `firing` opens an item that stays
  open. Needs a maximum age or a reconcile job against the monitoring system.
- **Escalation is not enforced.** The time to escalate is written into the ticket and checklist,
  but nothing re-pages when it passes.
- **Priority only goes up.** A lower repeat does not lower the priority; a person decides that.
- **Unbounded memory.** The processed-id list grows forever; it needs a retention period.
- **Rejected payloads are re-sent on every run** to the dead letter (with the same key, so a
  receiver drops them), because they have no reliable id to remember.
- **Simple severity model.** Latency is never P1, there is no notion of how many customers are
  affected, and only four alert types are known.
- **Wording not reviewed by owners.** The checklist reflects my own on-call practice; the teams who
  own the runbooks should review it.

## Time spent and next steps

Approximately TODO hours in total, including reading the exercise, design decisions, reviewing and
running the code, and writing the documentation.

With another day I would:

1. Replace the state file with SQLite/Postgres and unique constraints, so parallel runs are safe.
2. Add the Alertmanager webhook receiver and an outbox worker with retries, keeping the CLI for
   batch and testing.
3. Add a maximum age for open items and a reconcile step against the monitoring system.
4. Add an escalation timer that re-pages the escalation contact when the time passes.
5. Record the rules/catalog version with every decision.
6. Have the checklist wording reviewed by the teams who own the runbooks.
