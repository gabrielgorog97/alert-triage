# AI-assisted development process

## Tools

- Cursor IDE agent (Claude) for design discussion, code proposals, explanations and review.

## Log

Kept as I worked, newest last. Each entry says what I asked, what came back, and what I decided.

### Session 1 (started 11:11)

- Asked the assistant to read the five options and recommend one. It suggested Option 2 because it
  matches my day-to-day work (NOC/SOC alert handling, runbooks, escalation). I agreed.
- Asked for a plan where I make the operational decisions (severity rules, thresholds, checklist
  content) and the assistant proposes structure and code that I review and run.
- Design. The assistant proposed the alert format, a service catalog, and four outcomes
  (triaged, duplicate, manual review, rejected). It offered options for each rule; I chose:
  - P1-P4 instead of SEV1-3, because that is the scale I use with Opsgenie at work.
  - P1 only for prod, customer-facing, and service down, dependency down, or errors at 2x threshold.
  - P3 for prod internal services, P4 for staging; neither pages anyone.
  - Duplicates stay duplicates until a resolved alert closes them, not a fixed time window.
  - Escalation after 15 min for P1, 30 min for P2, none for P3/P4.
- The assistant first wrote the staging answer for a 3-level scale; after I picked 4 levels it
  asked where staging goes instead of guessing, and I split P3/P4 as above.
- Two rules were the assistant's proposal, which I accepted: ignore a redelivered `alert_id`, and
  send threshold alerts without numbers to manual review instead of rejecting them.
- Core code. The assistant proposed four small modules (models, validation, catalog, rules) using
  only the standard library plus PyYAML. It suggested hand-written validation over a library like
  pydantic so that every error message and rule is visible and easy to explain; I agreed.
- The first version used `class Outcome(str, Enum)`; ruff flagged it (UP042) and it was changed to
  `StrEnum`.
- Choices in the generated code I checked and kept: validation reports every problem at once
  instead of stopping at the first one; booleans are not accepted as numbers (`True` is an `int` in
  Python); a broken catalog stops the run with an error instead of sending every alert to manual
  review; a threshold of 0 or less goes to manual review, because "2x threshold" would make every
  value a P1.
- State and outbox. The assistant proposed a JSON state file with atomic writes, a `Notifier`
  interface with a file-based outbox, and an engine that runs the README steps in order.
  Proposals from the assistant that I reviewed:
  - A duplicate that would now be a higher priority raises the open item's priority and pages,
    so deduplication cannot hide a problem that got worse.
  - Send notifications before saving state, with idempotency keys, so a crash can repeat a page but
    not lose one (at-least-once delivery, made safe by the receiver dropping repeated keys).
  - Duplicates do not send any notification; the count is kept in state.
  I agreed with all three.
- Checklists. Asked for checklist templates in a data file (not code) so operators can edit them.
  The assistant wrote a first draft of the wording (assess, evidence, mitigate, escalate, close,
  plus a manual-review checklist); I reviewed it against what I do on shift (see next entry).
- My changes to the checklist draft, from how I handle alerts at work:
  - First step is reading the alert (priority, description, service, environment) and opening the
    runbook before anything else; the draft started with acknowledging the ticket.
  - Evidence I collect for every alert type, not only some: Grafana dashboards, Kubernetes state,
    logs, testing the service myself as a customer, recent code/deploy changes, and planned
    maintenance. These became a common evidence section.
  - Change window: "the hours before the alert started", anchored to `started_at`, instead of a
    fixed "last 2 hours" from now.
  - Explicit limits: do not do steps that belong to another team (escalate instead); never escalate
    without the evidence and what was already checked; never close without writing what was
    checked, what happened and how it was resolved.
- Adding the guardrail made two tests fail: they searched for the text "escalate to", which now
  also appears in the guardrail. The checklist was right; the tests were too loose and now match
  the exact escalation step.

## Accepted, changed, rejected

To be filled in as decisions are made.

## Verification

To be filled in.

## Limitations and risks

To be filled in.
