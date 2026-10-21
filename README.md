# alert-triage

A small CLI that reads synthetic alerts and a service catalog, then produces a triage result and a
first-response checklist for the on-call engineer. Its output is an alert in Opsgenie (simulated in
a local file). Take-home exercise, Option 2 (Alert Triage and Runbook Assistant).

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
- never creates unbounded work: repeats of the same problem update one open Opsgenie alert;
- never touches production: its only actions are simulated Opsgenie calls in a local file;
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
- `--outbox` is where the simulated Opsgenie calls and rejected payloads are written
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
`examples/alerts/` has eleven files processed in order with one shared state:

| File | Alert | Result | Rule that produced it |
|---|---|---|---|
| `01-error-rate` | HighErrorRate, checkout-api prod, 0.07 vs 0.05 | **P2**, Opsgenie alert OG-0001, pages EOC on-call (ack within 15 min) | prod and customer-facing; 1.4x threshold is below the 2x line for P1 |
| `02-latency` | HighLatency, search-api prod, 1.8s vs 0.5s | **P2**, OG-0002, pages EOC on-call | prod and customer-facing; latency alone is never P1 (slow is not down) |
| `03-dependency-down` | DependencyDown, checkout-api prod | **P1**, OG-0003, pages EOC on-call (ack within 5 min), open an incident | prod, customer-facing, outage-type alert |
| `04-repeats` (3 alerts) | `a-1001` again | **ignored** | same `alert_id` already processed: a redelivery |
| | `a-1002`, 0.08 | **duplicate** of OG-0001, no new work | same name + service + environment still open |
| | `a-1003`, 0.15 | **duplicate**, OG-0001 raised from P2 to P1, pages again | 3x threshold is P1; a worse repeat must not stay hidden |
| `05-internal` | ServiceDown, report-builder prod | **P3**, OG-0004 to EOC on-call, no page | prod but internal; no customer impact expected |
| `06-staging` | ServiceDown, search-api staging | **P4**, OG-0005 to EOC on-call, no page | non-prod, even for an outage |
| `07-unknown-service` | HighErrorRate, loyalty-points prod | **manual_review**, OG-0006 to `eoc-triage` | service not in the catalog: no owner, no runbook, so no guessing |
| `08-malformed` | missing service, bad time, text value | **rejected**, OG-0007 to `eoc-triage` + dead letter | validation: all three errors reported at once; it may still be a real alert, so a person checks it |
| `09-not-json` | truncated file | **rejected**, OG-0008 to `eoc-triage` + dead letter | file cannot be parsed; the raw text goes in the Opsgenie alert and the rest of the batch continues |
| `10-resolved` | resolved, checkout-api prod | **resolved**, OG-0001 closed | closes the open alert with the same fingerprint |
| `11-back-again` | HighErrorRate, checkout-api prod, 0.09 | **reopened**: OG-0009, linked to OG-0001, pages | the same problem came back within 30 minutes of closing; Opsgenie cannot reopen a closed alert, so the new one points to the old one |

Exit code is 4, because three alerts need a person. Running `make demo`'s command a second time
on the same state creates no new Opsgenie alerts or pages: every alert is a redelivery, and the
two broken files are recognised by their content and point to their existing Opsgenie alerts.

Default output, one line per alert:

```text
PRI  ALERT              SERVICE/ENV          TYPE            RESULT         OPSGENIE  PAGE  WHY
P2   a-1001             checkout-api/prod    HighErrorRate   triaged        OG-0001   yes   prod, customer-facing, HighErrorRate above threshold
P2   a-2001             search-api/prod      HighLatency     triaged        OG-0002   yes   prod, customer-facing, HighLatency above threshold
P1   a-3001             checkout-api/prod    DependencyDown  triaged        OG-0003   yes   prod, customer-facing, DependencyDown
--   a-1001             checkout-api/prod    HighErrorRate   ignored                        alert_id already processed (redelivery)
P2   a-1002             checkout-api/prod    HighErrorRate   duplicate      OG-0001         already open as OG-0001, seen 2 times
P1   a-1003             checkout-api/prod    HighErrorRate   duplicate      OG-0001   yes   already open as OG-0001, seen 3 times; priority raised P2 -> P1
P3   a-5001             report-builder/prod  ServiceDown     triaged        OG-0004         prod, internal service (not customer-facing)
P4   a-6001             search-api/staging   ServiceDown     triaged        OG-0005         non-prod environment 'staging'
--   a-7001             loyalty-points/prod  HighErrorRate   manual_review  OG-0006         service 'loyalty-points' is not in the catalog
--   08-malformed.json                                       rejected       OG-0007         'service' is required and must be a non-empty string (+2 more)
--   09-not-json.json                                        rejected       OG-0008         alert file is not valid JSON: Expecting ',' delimiter: line 2 column 1 (char 70)
--   a-1004             checkout-api/prod    HighErrorRate   resolved       OG-0001         closed OG-0001 after 3 alerts
P2   a-1005             checkout-api/prod    HighErrorRate   reopened       OG-0009   yes   prod, customer-facing, HighErrorRate above threshold; back within 30 min of closing OG-0001, linked to it (reopen 1)

Summary: 13 alerts: 2 duplicate, 1 ignored, 1 manual_review, 2 rejected, 1 reopened, 1 resolved, 5 triaged. Needs human review: 3.
```

With `--details`, each triaged or manual-review alert also shows impact, escalation and its
checklist:

```text
[P2] a-1001  HighErrorRate  checkout-api/prod  -> triaged  OG-0001  owner=payments-team  page=yes
     what: 7% of checkout requests return 5xx over the last 5 minutes
     impact: Customers likely affected: checkout-api is customer-facing in prod (HighErrorRate: some requests fail).
     why: prod, customer-facing, HighErrorRate above threshold; opened OG-0001
     pages eoc-oncall; not acknowledged in 15 min -> Opsgenie escalates to payments-oncall-lead
     escalate when the runbook says so: payments-oncall-lead
     checklist:
       1. Acknowledge Opsgenie alert OG-0001 so others know it is being handled. Do it within 15 minutes: after that Opsgenie escalates it automatically to payments-oncall-lead.
       2. Read the alert: priority P2, what happened, service checkout-api, environment prod.
       3. Check customer impact: is checkout-api in prod failing for users right now?
       4. Open the runbook before changing anything: https://runbooks.example.com/checkout-api
       5. Save the relevant Grafana dashboard panels (traffic, errors, latency) for the time around 2026-10-21T08:00:00+00:00.
       6. Check the Kubernetes state of checkout-api: pod status, restarts, recent events.
       7. Collect logs from checkout-api for the time around 2026-10-21T08:00:00+00:00; add a sample as a note on the Opsgenie alert.
       8. Test checkout-api yourself as a customer would (call the affected endpoint) and record the result.
       9. Check code, deploy and config changes to checkout-api in the hours before 2026-10-21T08:00:00+00:00.
       10. Check whether planned maintenance or a change window covers checkout-api or its dependencies.
       11. Note the error rate (0.07 vs threshold 0.05) and group errors by endpoint and status code.
       12. Check the dependencies listed in the runbook for errors at the same time.
       13. Only do steps the runbook gives to the on-call / EOC role. If a step belongs to another team (for example engineers restart the service), escalate to them instead of doing it yourself.
       14. If a change lines up with the start time, follow the runbook's rollback procedure.
       15. If the runbook says to escalate, or you cannot mitigate it, escalate to payments-oncall-lead. Include what you checked, the evidence collected, and what you already tried.
       16. Before closing Opsgenie alert OG-0001, add a note with what was checked, what happened, and how it was resolved. Do not close it without this.

[--] a-7001  HighErrorRate  loyalty-points/prod  -> manual_review  OG-0006  owner=eoc-triage  page=no
     what: errors on a service nobody added to the catalog
     impact: Unknown: the rules cannot decide (see why); assess impact by hand.
     why: service 'loyalty-points' is not in the catalog; opened OG-0006
     checklist:
       1. Do not change anything yet. Confirm the alert is real by checking the monitor that sent it.
       2. Find the owner of 'loyalty-points': search the service inventory, ask in the EOC channel.
       3. Decide the priority by hand, set it on Opsgenie alert OG-0006, and add a note with the reason.
       4. When handing over, include what you checked and why the alert was unclear.
       5. Remove the ambiguity for next time: add the service, environment or alert type to the catalog or fix the alert definition.
```

`--json` prints the same results as objects (`priority`, `impact`, `owner`, `escalation`,
`page`, `responder`, `ack_timeout_minutes`, `reasons`, `checklist`, `opsgenie_id`, ...) plus a
`summary` with counts. Each line in `outbox/events.jsonl` is one simulated Opsgenie call with its
idempotency key, for example:

```json
{"action": "create_alert", "idempotency_key": "create_alert:HighErrorRate|checkout-api|prod:OG-0001", "opsgenie_id": "OG-0001", "alias": "HighErrorRate|checkout-api|prod", "responders": ["eoc-oncall"], "page": true, "priority": "P2", "owner": "payments-team", "escalation": "payments-oncall-lead", "ack_timeout_minutes": 15, "impact": "Customers likely affected: ...", "checklist": ["..."]}
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
| `triaged` | priority, owner and checklist decided | Opsgenie alert; it pages for P1/P2 |
| `duplicate` | same alert already open | none, the open alert's counter goes up (or its priority is raised and it pages) |
| `manual_review` | readable, but the rules cannot decide | Opsgenie alert to `eoc-triage`, no page |
| `rejected` | cannot be read or validated | Opsgenie alert to `eoc-triage` with the raw payload, plus the dead letter |
| `resolved` | monitoring reports recovery | the open Opsgenie alert is closed |
| `reopened` | same problem back within 30 minutes of closing | new Opsgenie alert linked to the closed one; pages again for P1/P2 |
| `ignored` | redelivered `alert_id`, or resolved with nothing open | none |

`manual_review` and `rejected` are different on purpose: "I don't know what this means" is a
decision for a person, "I can't parse this" is broken data. Neither is dropped: both open an
Opsgenie alert for a person, because a broken alert can still be a real outage.

### Triage rules

Evaluated in this order; the first rule that applies wins. Code: `src/alert_triage/rules.py` and
`engine.py`.

1. **Validation.** `alert_id`, `name`, `service`, `environment`, `status` (`firing` or `resolved`)
   and `started_at` (ISO 8601) are required and must have the right types. Otherwise: `rejected`,
   with an Opsgenie alert to `eoc-triage` holding the raw payload. The same broken payload arriving
   again is recognised by a hash of its content and points to the existing Opsgenie alert.
2. **Redelivery.** An `alert_id` that was already processed is ignored, so a retried delivery
   changes nothing.
3. **Resolved.** `status: resolved` closes the open Opsgenie alert with the same fingerprint, if any.
4. **Duplicate.** Fingerprint = `name + service + environment`, used as the Opsgenie `alias`. If an
   alert with that fingerprint is open, it is a `duplicate` until a `resolved` alert closes it. A
   duplicate never opens a new Opsgenie alert, but if it would get a higher priority than the open
   one (for example errors grew from 1.5x to 3x threshold), the open alert's priority is raised
   and the new priority pages if it should.
5. **Reopen.** If the same fingerprint was closed less than 30 minutes ago, the new Opsgenie alert
   is linked to the closed one (`previous_alert`, outcome `reopened`). Opsgenie cannot reopen a
   closed alert, so the link is what keeps the history together. On the third return the alert is
   tagged `flapping`: it keeps recovering and failing, and its owner should tune it. Only one
   Opsgenie alert per fingerprint is ever open.
6. **Manual review** when any of these is true:
   - the service is not in the catalog
   - the environment is not one the catalog lists for that service
   - the alert name is not a known type
   - a threshold alert (`HighErrorRate`, `HighLatency`) has no `value` or `threshold`, or the
     threshold is 0 or less
7. **Priority:**

| Priority | When | Notify | Automatic escalation |
|---|---|---|---|
| P1 | prod, customer-facing, and `ServiceDown`, `DependencyDown`, or error rate at least 2x threshold | Opsgenie alert to EOC on-call, pages; open an incident | only if not acknowledged in 5 min |
| P2 | prod, customer-facing, any other known alert | Opsgenie alert to EOC on-call, pages | only if not acknowledged in 15 min |
| P3 | prod, internal service (not customer-facing) | Opsgenie alert to EOC on-call, no page | none |
| P4 | any non-prod environment (staging) | Opsgenie alert to EOC on-call, no page | none |

Opsgenie also uses P1 to P5, so the priority is passed through unchanged.

**First response is always EOC.** Every alert, whatever its priority, goes to the EOC on-call;
the service team is the `owner` on the alert and the place to escalate to. What differs is how
loudly EOC is told: `page: true` (P1/P2) means the alert is expected to wake someone up. The
channel itself (phone, SMS, push, email) comes from each engineer's Opsgenie notification rules,
so P3/P4 arrive quietly, as push or email, unless someone chooses otherwise.

**Escalation.** The EOC on-call engineer investigates. Escalating to
the service team (`escalation` in the catalog) is that engineer's decision, when the runbook says
so or the problem cannot be mitigated; there is no timer on it. The only automatic escalation is
for a missed alert: if nobody acknowledges it in time, Opsgenie's escalation policy notifies the
service's escalation contact. That timer lives in Opsgenie, not in this program.

Known alert types: `HighErrorRate`, `HighLatency`, `DependencyDown`, `ServiceDown`.

**Customer impact** is stated for every alert: "Customers likely affected" for P1/P2 (with what
customers experience for that alert type), "No direct customer impact expected" for P3, "No
customer impact" for P4, and "Unknown" for manual review.

### Checklists

Templates live in `src/alert_triage/data/checklists.yaml`, not in code, so the people who run the
shift can change the wording without a code change (or pass their own file with `--checklists`).
The order is deliberate:

1. Acknowledge the Opsgenie alert first: it stops the escalation timer and tells others someone
   is on it. Paged alerts also say how long there is before Opsgenie escalates.
2. Read the alert, check impact (customers in prod; "who is blocked" in non-prod), then open the
   runbook before changing anything.
3. Collect evidence for every alert type: dashboards, Kubernetes state, logs, test as a customer,
   changes in the hours before the alert started, planned maintenance. Then the evidence specific
   to the alert type.
4. Guardrail: only steps the runbook gives to the on-call role; other teams' steps are escalated.
5. Mitigation from the runbook.
6. Escalation when the runbook says so or it cannot be mitigated, with the evidence already
   collected; P1 also opens an incident.
7. Do not close the Opsgenie alert without a note on what was checked, what happened and how it
   was fixed.

A template file that is missing a section or an alert type stops the run (exit 3), instead of
producing an incomplete checklist.

### State and notifications

- `state/state.json` remembers processed alert ids and open items between runs. It is written to a
  temporary file and renamed, so a crash mid-write keeps the previous state. An unreadable state
  file stops the run, because starting empty would open a new Opsgenie alert for every known
  problem.
- Opsgenie calls and rejected payloads go through a `Notifier` interface (`outbox.py`).
  `FileOutbox` appends them to `outbox/events.jsonl`; tests use `MemoryOutbox`. A real Opsgenie
  client would implement the same single method.
- The simulated calls are `create_alert`, `update_priority`, `close_alert` and `dead_letter`.
  There is no separate "page" call: in Opsgenie, creating a P1/P2 alert for the EOC team is what
  notifies the on-call engineer (`page: true` in the payload).
- Every call has an idempotency key: `create_alert:<fingerprint>:<opsgenie id>`,
  `update_priority:<alert_id>`, `close_alert:<opsgenie id>`, and for rejected payloads, which may
  have no usable id, `dead_letter:<hash>` and `create_alert:rejected:<hash>` (hash of the payload
  content). Calls are made before state is saved, so a crash in between can repeat a call with
  the same key, but never lose a page.

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

90 tests, no network, each with a temporary state and outbox:

| Area | File | Examples |
|---|---|---|
| Normal alerts | `test_rules.py`, `test_engine.py` | every priority, owner, escalation, impact statement |
| Boundaries | `test_rules.py` | exactly 2x threshold is P1, just below stays P2, threshold 0 |
| Malformed | `test_validation.py`, `test_engine.py`, `test_cli.py` | missing fields, wrong types, booleans as numbers, bad time, not JSON, not an object; each gets one Opsgenie alert for EOC triage, also across runs |
| Duplicate | `test_engine.py`, `test_cli.py` | repeats, redelivery, priority raise, re-running the whole batch |
| Flapping | `test_engine.py` | back within 30 min is linked to the closed alert, unlinked after, `flapping` tag on the 3rd return, survives restart |
| Ambiguous | `test_rules.py`, `test_engine.py` | unknown service, environment, alert type, missing numbers; repeated ambiguous alert gives one Opsgenie alert |
| Failure paths | `test_catalog.py`, `test_checklist.py`, `test_engine.py`, `test_cli.py` | broken catalog, broken templates, corrupt state, missing input, exit codes |
| Checklists | `test_checklist.py` | evidence before mitigation, placeholders filled, non-prod impact question |
| Output | `test_cli.py` | one-line table, HTML report written and HTML-escaped |

## Connecting to real systems

How this design would be connected to monitoring, Opsgenie (paging and incidents) and chat, while
staying idempotent, auditable and safe to retry. None of this is implemented.

**Monitoring in.** A small HTTP receiver (for example for Prometheus Alertmanager webhooks)
converts each alert into the input format and calls `TriageEngine.process`. Alertmanager has no
per-delivery id, so `alert_id` would be derived from the alert's fingerprint and `startsAt`, and
`status: resolved` maps directly. The receiver answers 2xx only after the state is saved; if it
fails, the sender retries and the redelivery rule makes the retry harmless.

**State.** The JSON file becomes a database table with unique constraints on `alert_id` and on the
open fingerprint, and each alert is handled in one transaction. That gives correct behaviour when
several receivers run in parallel, which the file cannot.

**Calls to Opsgenie (outbox pattern).** Instead of calling Opsgenie inside the alert
transaction, the engine writes the intended call with its idempotency key into an outbox table
in the same transaction. A separate worker delivers them with retries and backoff, and moves
calls that keep failing to a dead-letter queue that someone is alerted on. An Opsgenie outage
then delays alerts but does not lose them or block triage.

- **Deduplication:** the Opsgenie Alert API deduplicates on `alias` while an alert with that alias
  is open. Sending the fingerprint as `alias` means a repeated create does not open a second alert
  or page twice; Opsgenie only increases the alert's count.
- **Close and update:** close and priority calls address the alert by its alias, so the worker
  does not need to know Opsgenie's own alert id.
- **Routing and escalation:** responders, notification rules and the ack-timeout escalation live
  in Opsgenie team settings and escalation policies; this tool only sends the priority and the
  team.
- **Incidents:** the P1 step "open an incident" maps to an Opsgenie incident, opened by the
  engineer, not automatically.
- **Chat:** a message per P1/P2 to the EOC channel, with the message id stored so updates
  edit the same message. A repeated chat message is the least harmful failure, so it is accepted.

**Audit.** Every decision is already a record: input payload, outcome, reasons, rule inputs,
Opsgenie alert id, timestamps. In production that log is append-only, kept for the incident review, and each
record also stores the version of the rules, catalog and checklists (git commit) that produced it,
so "why did this page at 3 a.m.?" has an exact answer.

**Safe to operate.**

- The tool only creates, updates and closes Opsgenie alerts; it never runs remediation. Changes stay with people and
  their runbooks.
- Catalog, rules and checklists live in git and change through review, like code.
- It fails closed: a broken catalog or state stops processing and is reported, instead of silently
  sending everything to the wrong place.
- Rollout in shadow mode first (decide and log, but do not page) next to the current routing, and
  compare decisions before switching.
- The tool itself needs monitoring: alerts processed, manual-review rate, dead-letter count,
  delivery failures and age of the oldest undelivered action.

## Assumptions

- Opsgenie is the alerting and paging tool. The exercise asks for "notifications or ticket
  creation", so there is no separate ticketing system: the Opsgenie alert is the unit of work.
- EOC does first response for every alert, so all priorities go to the EOC on-call (`eoc-oncall`);
  only P1/P2 page. The owning team is escalated to by EOC when it cannot fix or troubleshoot it.
- Manual-review and rejected items go to a single EOC triage team (`eoc-triage`), since the owner
  may be unknown.
- A threshold alert without numbers is incomplete data (manual review), not malformed input,
  because the rest of the alert is usable.
- Only `prod` is production; every other environment listed in the catalog is non-prod.
- An alert's `name` is its type; the same type on the same service and environment is the same
  problem until it resolves.
- Opsgenie ids (`OG-0001`) are local and only stand in for real Opsgenie alert ids.

## Known limitations

- **Single process.** The state file has no locking; two runs at the same time can overwrite each
  other. Fine for a CLI, not for a service.
- **Lost or out-of-order resolve.** An item stays open until a `resolved` alert arrives. If it is
  lost, the Opsgenie alert never closes and new alerts for it only count as duplicates. A `resolved` that
  arrives before its `firing` alert is ignored, and the later `firing` opens an item that stays
  open. Needs a maximum age or a reconcile job against the monitoring system.
- **No acknowledgement timer here.** The ack timeout and the escalation contact are sent with the
  alert, but this program does not wait for an acknowledgement; Opsgenie's escalation policy does.
- **Re-notifying on a priority raise is not verified.** When a duplicate raises an open alert to
  P1, the payload asks for a page. Whether Opsgenie notifies again on a priority change depends on
  its notification rules; a real client must make sure it does.
- **Priority only goes up.** A lower repeat does not lower the priority; a person decides that.
- **Unbounded memory.** The processed-id list grows forever; it needs a retention period.
- **Recovery closes the Opsgenie alert automatically.** When monitoring sends `resolved`, the
  alert is closed without a human note. This is a deliberate choice (it matches how alerting tools
  auto-resolve), but it is in tension with the checklist rule "do not close without writing what
  happened": the note has to be added after the fact.
- **Flapping still pages.** A returning P1/P2 pages again each time, even once tagged flapping.
  Safer than going quiet on a real outage, but noisy until the alert is tuned.
- **Reopen window uses processing time**, not the alert's own timestamps, so replaying old files
  in a batch treats them as happening now.
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
4. Define the Opsgenie escalation policies (who is primary, ack timeouts) as code, so the policy
   lives in one reviewed place.
5. Record the rules/catalog version with every decision.
6. Have the checklist wording reviewed by the teams who own the runbooks.
