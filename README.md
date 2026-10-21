# alert-triage

A small CLI that reads synthetic alerts and a service catalog, then produces a triage result and a
first-response checklist for the on-call engineer. Take-home exercise, Option 2 (Alert Triage and
Runbook Assistant).

> Work in progress. Sections below are filled in as the work is done.

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
  "value": 0.12,
  "threshold": 0.05,
  "started_at": "2026-10-21T08:00:00Z"
}
```

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
| `duplicate` | same alert already open | none, the open item's counter goes up |
| `manual_review` | readable, but the rules cannot decide | ticket to the EOC triage queue |
| `rejected` | cannot be read or validated | stored in a dead-letter file |
| `resolved` | closes the open item | resolve note on the existing ticket |

`manual_review` and `rejected` are different on purpose: "I don't know what this means" is a
decision for a person, "I can't parse this" is broken data.

### Triage rules

Evaluated in this order; the first rule that applies wins.

1. **Validation.** `alert_id`, `name`, `service`, `environment`, `status` (`firing` or `resolved`)
   and `started_at` (ISO 8601) are required and must have the right types. Otherwise: `rejected`.
2. **Redelivery.** An `alert_id` that was already processed is ignored, so a retried delivery
   changes nothing.
3. **Resolved.** `status: resolved` closes the open item with the same fingerprint, if any.
4. **Duplicate.** Fingerprint = `name + service + environment`. If an item with that fingerprint is
   open, it is a `duplicate` until a `resolved` alert closes it.
5. **Manual review** when any of these is true:
   - the service is not in the catalog
   - the environment is not one the catalog lists for that service
   - the alert name is not a known type
   - a threshold alert (`HighErrorRate`, `HighLatency`) has no `value` or `threshold`
6. **Priority:**

| Priority | When | Notify | Escalate if not mitigated |
|---|---|---|---|
| P1 | prod, customer-facing, and `ServiceDown`, `DependencyDown`, or error rate at least 2x threshold | page + ticket | after 15 min |
| P2 | prod, customer-facing, any other known alert | page + ticket | after 30 min |
| P3 | prod, internal service (not customer-facing) | ticket | no automatic escalation |
| P4 | any non-prod environment (staging) | ticket | no automatic escalation |

Known alert types: `HighErrorRate`, `HighLatency`, `DependencyDown`, `ServiceDown`.

### Notifications

Tickets and pages are written to a local `outbox/` directory through a small adapter interface,
so a real paging or ticketing integration can replace it without changing the triage logic.

## Assumptions

- Manual-review items go to a single EOC triage queue, since the owner may be unknown.
- A threshold alert without numbers is treated as incomplete data (manual review), not as
  malformed input, because the rest of the alert is usable.
