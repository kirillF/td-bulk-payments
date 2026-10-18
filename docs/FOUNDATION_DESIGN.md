# Design Freeze

Status: DRAFT
Invariant approval: AWAITING_EXACT_REQUEST
Risk approval: AWAITING_EXACT_REQUEST
Budget approval: AWAITING_EXACT_REQUEST
Approved by: Awaiting operator decision on the exact foundation request
Approval evidence: Scope discussion accepted with operator message "approve the scope"; Python version amended by "use latest python 3.15". Neither message approves installation bytes.

## Outcome

Install the reviewed Python 3.15 Firm Payments development foundation without payment feature behavior.

## Scope

- In: app shell, locked Python dependencies, lint/format/type tooling, Docker/Compose, CI, README and harness guidance/provenance/tracker bootstrap.
- Out: payment endpoint behavior, database migrations, real transactions, authentication, deployment, publishing and formal acceptance campaign.

## Invariants

- Installation consumes only the immutable approved preview and binds existing file preimages.
- Preserve Git history and unrelated files; the existing .gitignore is preserved with appended tooling entries.
- No payment behavior or payment verification is claimed by scaffold installation.
- Subsequent implementation preserves atomicity, integer cents, nonnegative balances, fund conservation, indexed unique firm UUIDs and payer-scoped durable idempotency as agreed in the foundation profile.

## Weaknesses and risks

- Weakest current evidence is container portability: macOS Python dependency installation is verified; Linux container build and payment behavior are not yet verified.
- The host Homebrew Python 3.15 pyexpat extension is broken; preview checks use an isolated uv-managed Python 3.15. Default host setup may require a working managed interpreter.
- Image and CI-action major tags can change upstream; Python packages are exactly locked.
- Installation creates a local foundation commit and Beads tracker. Failures retain managed preview and audit records for identical-input recovery; no destructive reset.

## Assumptions and unknowns

- Assumption: this is a take-home with test data; real-payment operations and external deployment remain outside scope.
- Assumption: the accepted third table and required idempotency header are intentional additions to the supplied exercise.
- Unknown: Linux image and Testcontainers payment scenarios still require verification during implementation.

## Ownership and decision rights

- Agent: prepare preview, perform disposable verification and install only after verified exact approval; own routine task bookkeeping and checks.
- Operator: architecture, public API/schema scope, exact foundation installation approval, external publication and formal acceptance when requested.
- External or irreversible actions: no publication or deployment; only the approved local foundation commit and tracker initialization.

## Resource budget

- Hard limits: target repository and selected harness state/approval store only; disposable verification in temporary storage; no agents, paid services or external publication.
- Working allocation: one coherent minimal foundation and justified corrections; bounded commands and retained failure evidence.
- Verification reserve: at least 20 percent of working effort for checks, evidence and handoff.
- Current consumption: architecture clarification, environment probes, isolated lock resolution/install and scaffold checks only.
- Remaining budget: minimal final preview inspection, exact operator decision, then installation and focused verification; no numeric user cap was supplied.
- Stop conditions: missing/changed approval, changed scope/authority, unresolved environment blockers or insufficient verification reserve. Routine capacity remains coordinator-managed without another budget ceremony.

## Acceptance scenarios

- Expected path: reviewed exact request receives explicit operator approval; immutable payload verifies; installation matches approved files and provenance.
- Negative path: no approval or changed preview bytes prevents installation.
- Recovery path: failed installation retains exact-input records and preview; resume through the supported harness path without rewriting history.

## Verification

- Required: review every scaffold file; check exact dependency lock, formatting, lint, strict typing, ASGI startup and target preimages.
- Adversarial check: confirm that no payment route is installed and no existing unrelated file changes are included.
- Report Linux Docker and payment integration tests as unrun until actually exercised.

## Change control

- Changed scope, invariants, authority or explicit resource cap requires operator decision; changed request bytes require fresh audited approval.
- Private implementation and routine checks stay within the existing agreed boundaries.

## Approval

- Requested decision: one exact foundation installation approval covers these invariants, risks, scope and coordinator-managed resource envelope.
- Current decision: not yet supplied for foundation installation. The earlier scope approval is design feedback only.
