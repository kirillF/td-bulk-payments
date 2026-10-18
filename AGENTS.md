# Repository guide

## Where to look

- Code changes: `CODING_STANDARDS.md`.
- Stack/tooling: `docs/harness/FOUNDATION.md`.
- Gated work, commands and coordinator loop: `docs/harness/README.md` and `docs/harness/OPERATIONAL_CONTRACT.md`.

## Working rules

- Coordinator: claim Beads work before acting; record evidence/status before yielding. Workers follow assigned ownership and leave Beads/approvals to the coordinator.
- Agree one useful outcome and constraints,then use a direct edit/run loop. The coordinator manages routine budgeting without approval of time pools or review/retry counts. Routine corrections within that outcome need no new approval package. Escalate changed scope,public interfaces,authority,external publication or explicit operator caps.
- Formal delivery and release are explicitly requested operations; preserve their records without fabricating completed gates.
- Use focused checks when requested or useful; report failed and unrun verification honestly.
- Isolated implementation workers continue automatically in fresh sessions through real Beads children with DONE,LEFT,next action and full parent input. Preserve partial work,scope,original acceptance and aggregate timeout/attempt counters. Monitored usage triggers early rollover,not a hard token ceiling. Coordinator-managed agents outside that transport must checkpoint proactively before context exhaustion.
- Keep progress/blockers in Beads and `.harness/`; keep this guide stable. Update command/path references when they change.

## Commands (repository root)

### setup

```sh
uv sync --locked --all-groups --managed-python
```

### start

```sh
uv run --locked uvicorn firm_payments.app:app --host 0.0.0.0 --port 8000
```

### check

```sh
uv run --locked python scripts/check.py
```
