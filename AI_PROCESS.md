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
- CLI and packaging. Asked for a command a person can run on a folder of alerts, with exit codes a
  script can act on. The assistant wrote `cli.py` (0 = OK, 2 = bad usage, 3 = config or input
  problem and nothing processed, 4 = finished but some alerts need a human), example fixtures that
  hit every outcome, a Makefile, a CI workflow and a non-root Dockerfile.
- Problems found while doing this step, all by the assistant reviewing its own code, not by me:
  - The dead-letter idempotency key used `alert_id`, so every malformed alert without an id got
    the same key `dead_letter:None` and a receiver would drop all but the first. Changed to a hash
    of the payload content and added a test.
  - `cli.py` had a leftover, meaningless return line in `build_parser`. Fixed before running.
  - Two new CLI tests failed on the first run. Both were test mistakes: one kept the redelivered
    copy of `a-1001` instead of the first one, the other read the text output of the first run
    as JSON. Fixed the tests; the program output was correct.
- Checked rather than assumed: the GitHub Actions versions in the CI file against the latest
  releases (checkout v7, setup-python v7, upload-artifact v7).
- Docker was not running when the image was first written, so it was left unverified. I started
  Docker later in the session and the image was built and run on the examples: same summary and
  exit code as the local run, running as uid 1000, not root.
- README pass. Before writing the README, the assistant compared the code with the exercise text
  and pointed out a gap: "identify likely customer impact" was only implied by the reason text.
  It proposed an explicit impact line per priority with draft wording; I accepted the wording.
  The impact is also added to the ticket and page payloads, so the person who opens the ticket
  sees it.
- The assistant also noticed that the checklist asked "is it failing for users?" on staging,
  where there are no real customers. I chose to replace it, for non-prod only, with "who is
  blocked: is staging needed for testing or a release right now?".

- Presentation. When I ran the demo, the full checklists for every alert made the terminal output
  too long to follow or present. I asked for something easier to read. The assistant offered a
  short table, an HTML report, both, or a full web app; I chose both and decided against a web app
  (more code to defend, not asked for). Default output is now one line per alert, `--details`
  shows the checklists, and `--html` writes a single-file report. The assistant checked the report
  in a browser and fixed two layout problems (ticket ids wrapping, long file names); a test makes
  sure alert text is HTML-escaped.

## Accepted, changed, rejected

**My decisions** (the assistant offered options, I picked): the P1-P4 scale and its rules,
P3 for internal prod and P4 for staging, deduplication until resolved, 15/30 minute escalation,
the checklist content and its guardrails, the non-prod impact question, verifying Docker rather
than dropping it, a short table plus HTML report instead of a web app.

**Accepted from the assistant after review:** the module layout and stdlib validation; ignoring
redelivered ids; manual review for threshold alerts without numbers and for thresholds of 0 or
less; raising priority on a worse duplicate; send-before-save with idempotency keys; content hash
for dead-letter keys; the exit codes; the impact wording.

**Changed:** the checklist draft (order, common evidence, change window anchored to the alert
start, guardrails); the staging rule, after the first draft assumed a 3-level scale;
`str, Enum` to `StrEnum` after the linter flagged it.

**Rejected or left out on purpose:** a validation library (pydantic), to keep every rule visible;
any real integration (paging, ticketing, chat), as the exercise requires; automatic remediation
steps such as restarts, because the tool should guide a person, not change production; lowering
priority on a duplicate, because a single lower reading does not prove the problem is smaller.

## Verification

- 84 automated tests (`make test`) covering normal alerts, every priority, boundaries
  (exactly 2x threshold, just below it, threshold 0), malformed and unreadable input, unknown
  service / environment / alert type, duplicates and redeliveries, priority raise, resolve,
  corrupt state, missing catalog, and the CLI exit codes.
- `ruff check` and `ruff format --check` in `make lint`; the same `make ci` target runs in the
  GitHub Actions workflow.
- `make demo` run by hand and the output read line by line against the README rules. Running it
  a second time on the same input creates no new tickets or pages.
- Wheel and sdist built with `make build`; Docker image built and run on the examples.
- I read every module and can explain each rule. Where a test failed, I checked whether the code
  or the test was wrong before changing anything (three times it was the test).

## Limitations and risks

Things I would not ship without more work (also listed in the README):

- State is a single JSON file with no locking. Two runs at the same time can overwrite each
  other. Production needs a database with unique constraints.
- An item stays open until a `resolved` alert arrives. If that alert is lost, the item never
  closes and new alerts for it are counted as duplicates. Needs a maximum age or a reconcile job
  against the monitoring system.
- Escalation times are written into the ticket but nothing enforces them; there is no timer.
- The processed-id list grows forever; it needs a retention period.
- The rules and checklist wording are mine and the assistant's, checked against my own shift
  experience, not reviewed by the owning teams.
