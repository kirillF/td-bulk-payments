# Project-local delivery policy

This policy applies only to `/Users/kirillf/devel/td-bulk-payments`.
Authority: `.harness/project-local-qual1/request.json`, SHA256
`a4d47cdc255ebc2e2081beb0b8badf0fcf3c1a3df06a369e788ee29f76024370`,
audit event `662c2ed383384c61b6324327e816b065` in the external selected approval store.
The immutable approved design defines the exception; this document implements it.

## Execution override

For this project, the trusted coordinator runs reviewed setup/check code directly,
including access to local Docker Desktop and Testcontainers. These checks replace the
common native sandbox/check transport. Host code and Docker access are trusted: they
can read host data and control the daemon. Resource labels and bounds prevent ordinary
cleanup mistakes; they do not create an adversarial security boundary.

Shared harness code, selectors, foundation authority and historical records remain
unchanged. Shared `plan/run/import` and native worker proofs are not used to certify this
local path. Every new evidence record identifies `project-local-trusted-host-v1`.

Before any setup/check run, `scripts/project_workflow.py` authenticates the exact external
approval using the pinned shared approval verifier and checks the approved design bytes.
Changed helper or approved design bytes stop execution for review.

## Local commands

```sh
.venv/bin/python scripts/project_workflow.py setup
.venv/bin/python scripts/project_workflow.py check local-policy
.venv/bin/python scripts/project_workflow.py check qualify-local
```

Setup uses `.uv-python` and `.uv-cache`, Python 3.15 and locked dependencies. Use an
existing Python 3.15 interpreter for the first invocation. Setup/network/Docker access
still requires the execution environment's normal permissions.

Only explicitly implemented check groups execute; future groups fail. Qualification
uses a dedicated non-root Python 3.15 HTTP fixture and real PG17. It does not implement
payment behavior or establish SC-26 acceptance. All application slices still implement
and verify their own registered groups before delivery.

## Stages and records

1. Preserve the four existing product/architecture/program/slice design stages and
   recorded operator feedback. Update only execution-policy references for this override.
2. Prepare a new exact feature request binding the reviewed designs, concrete slice
   ownership/dependencies/checks and current source manifest. Display it and record actual
   matching chat approval with the shared `record-chat-approval`; verify the immutable
   payload using `scripts/project_workflow.py` before execution. The QUAL-1 policy approval
   authorizes qualification only, not payment feature implementation.
3. Claim the dependency-ready Beads task. Execute sequentially, container delivery first.
   Candidate-bound check evidence and delivery records live in
   `.harness/project-local-workflow/`; retain failures. Source drift invalidates evidence.
   Record selected, baseline and prerequisite checks for each slice. Complete remaining
   check bindings before claiming those checks pass. No counters are reset.
4. Integration runs all required scenario/check groups against the exact final candidate.
   Review covers the complete source independently of implementation memory; use a fresh
   review process, or an exact audited operator review package with explicit findings and
   limitations. Missing review remains incomplete. Final acceptance has its own exact
   externally audited request and explicit operator decision. Publication is separate.

Project-local records reference shared historical task IDs and approvals; they never
fabricate shared imported deliveries or close native shared gates. Routine within-scope
fixes do not need repeated approval. Trust, ownership, product/API or invariant changes
require an exact new amendment.

## Failure and recovery

Commands have finite deadlines and bounded output. Nonzero exit, output exhaustion,
source drift, unavailable Docker and uncertain cleanup fail; none can become a PASS by
omission. Command logs redact explicitly registered synthetic secrets before retention.
Check authors must register canaries/credentials and avoid payload/driver error dumps.

Qualification journals resource ownership before creation. Ryuk is disabled only for this
qualification fixture because explicit per-run cleanup owns recovery and avoids mounting
the daemon socket in a sidecar. Cleanup targets a unique run label and exact run image tag;
it never prunes caches or removes existing Compose volumes. The supervising runner
attempts cleanup after failed/timed-out qualification. On interruption, inspect the retained
ownership journal and run:

```sh
.venv/bin/python scripts/qualify_local.py cleanup ABSOLUTE_RUN_DIRECTORY
```

Uncertain cleanup retains the journal and blocks QUAL-1 closure. A fresh qualification run
must use a new identity. Credentials are generated test-only values; application containers
receive no host directory or external approval-store mounts.
