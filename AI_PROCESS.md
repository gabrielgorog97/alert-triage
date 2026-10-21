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

## Accepted, changed, rejected

To be filled in as decisions are made.

## Verification

To be filled in.

## Limitations and risks

To be filled in.
