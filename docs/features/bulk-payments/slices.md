# Firm Payments — current slice contracts

This is the aligned target contract, not a record of new deliveries. feature.json contains
its matching task/check definitions and ownership. Historical approved requests, task IDs,
deliveries, failures and counters remain preserved under private harness state; replacing
current documentation neither reruns foundation nor reopens completed native gates.

## Outcome and derivation

Deliver FR-1–14, NFR-1–8, INV-1–7 and SC-1–38 (70 expanded cases). Requirements define
behavior; scenarios express acceptance; architecture/program design select mechanisms;
these slices assign implementation ownership and executable evidence. Concrete race counts,
fixture sizes and local profile values are verification/design choices, not product SLAs.

The original container-first five slices and subsequent sanitizer/async/versioned package
amendments are locally delivered history. Their current source is a prerequisite, not an
accepted final candidate. The three new migration slices below extend those contracts.
Fresh complete review and separate audited acceptance remain open.

## Dependency order and current ownership

```text
container-delivery -> atomic-bulk-api -> durable-idempotency
-> concurrency-and-bounds -> observable-operations -> sanitize-description-text
-> async-payment-lifecycle -> versioned-app-service-dotenv
-> alembic-container-runtime -> alembic-safe-adoption -> alembic-startup-integration
```

Container delivery remains first. For this amendment the first new slice delivers actual
Alembic resources/runtime in the production image; it precedes legacy-adoption and startup
integration work. Execute sequentially on the approved project-local trusted-host path.
The coordinator claims tasks, stages only owned work, authenticates evidence and records
local deliveries; source commits do not close delivery/review/acceptance gates.

## container-delivery

A clean checkout builds a Python 3.15 non-root image, starts local PostgreSQL, completes repeat-safe Alembic upgrade head and serves an HTTP application shell before payment capabilities are delivered.

Dependencies: none after approved foundation/qualification.

Requirements: NFR-4, NFR-5.

Invariants: INV-1, INV-6.

Scenarios: SC-26.

Acceptance:

- Execute SC-26: build the real Linux image and verify Python 3.15, non-root UID, PostgreSQL readiness, one-shot bootstrap ordering and HTTP application startup.
- Repeat bootstrap against seeded firms and verify existing rows/balances are preserved; probes/startup do not create payments or reserve idempotency keys.
- Materialize locked dependencies in a fresh isolated checkout; use explicit local resource settings without declaring production capacity.
- Deliver reusable owned container fixtures/check adapters for later real payment HTTP verification. Do not claim SC-18/21 or unimplemented payment/telemetry capabilities pass.
- Initial CI runs the implemented container/quality checks; selecting a future group fails explicitly, never returns placeholder PASS. The final slice extends CI to the full required suite.
- Use the approved project-local verification policy for execution. Exact feature approval and prerequisite deliveries remain mandatory; this task grants no shared-harness modification authority.

Owned paths (sequential shared-file extensions are explicit):

```text
Dockerfile
.dockerignore
compose.yaml
.github/workflows/ci.yaml
.python-version
scripts/check.py
scripts/harness_checks.py
tests/test_container.py
tests/conftest.py
examples/firms.sql
examples/bulk-payment.json
README.md
docs/operations.md
app/__init__.py
app/main.py
app/api/router.py
app/api/errors.py
app/api/operations.py
app/api/v1/payments.py
app/db/database.py
app/core/config.py
migrations/versions/0001_initial.py
app/db/migrate.py
alembic.ini
migrations/env.py
migrations/script.py.mako
pyproject.toml
uv.lock
```

Registered checks: baseline-syntax, quality, container-boot, local-policy.

## atomic-bulk-api

SC-21 and other single-request paths produce exact atomic balances/payment rows and the complete ordered response list with real generated IDs.

Dependencies: container-delivery.

Requirements: FR-1, FR-2, FR-3, FR-4, FR-8, FR-9, NFR-1, NFR-4.

Invariants: INV-1, INV-2, INV-3, INV-6.

Scenarios: SC-1, SC-2, SC-3, SC-4, SC-5, SC-6, SC-14, SC-17, SC-19, SC-21.

Acceptance:

- Bind every outline row for the assigned scenarios; SC-21 uses exact operator SQL/JSON and produces 3674875/170075/1405050-cent balances and three correctly identified response/payment rows.
- Validation/unknown firms/self-payment/total insufficiency/recipient overflow produce no monetary effects; exact balance and repeated payees succeed.
- Injected insertion failure after balance mutation rolls the batch back and produces the agreed sanitized retryable response.
- Unique indexed UUID lookup is checked with independently justified population/selectivity and real PostgreSQL plan evidence.
- Generate response IDs from actual inserted rows in request order, allowing sequence gaps; no inferred contiguous ranges.
- Register only implemented check groups. Missing future groups must fail, not return placeholder PASS. Preserve provisional replay behavior; do not claim FR-5–7 or observability delivered here.

Owned paths (sequential shared-file extensions are explicit):

```text
app/__init__.py
app/schemas/payments.py
app/schemas/headers.py
app/schemas/errors.py
app/models/payment.py
app/models/idempotency.py
app/core/config.py
app/db/database.py
app/services/payments.py
app/services/idempotency.py
app/main.py
app/api/router.py
app/api/errors.py
app/api/operations.py
app/api/v1/payments.py
migrations/versions/0001_initial.py
app/db/migrate.py
alembic.ini
migrations/env.py
migrations/script.py.mako
tests/test_models.py
tests/test_core.py
tests/test_index.py
scripts/harness_checks.py
scripts/check.py
pyproject.toml
uv.lock
tests/conftest.py
tests/__init__.py
tests/test_payments.py
tests/test_journeys.py
tests/features/bulk_payments.feature
```

Registered checks: baseline-syntax, quality, money-unit, atomic-api, uuid-index, local-policy.

## durable-idempotency

A payer/key executes at most once; normalized retries replay the entire original JSON list/IDs and 422 outcomes; conflicting reuse returns 409.

Dependencies: atomic-bulk-api.

Requirements: FR-4, FR-5, FR-6, FR-7, FR-8, NFR-4.

Invariants: INV-1, INV-2, INV-3, INV-4, INV-6.

Scenarios: SC-7, SC-8, SC-9, SC-10, SC-16.

Acceptance:

- Implement and bind fingerprint v1 equivalence/conflict cases, original response IDs/order and distinct payer key namespaces.
- Persist business 422 alongside its key; a deposit does not change an earlier decline; malformed input leaves no reservation.
- Record payments, balance effects and complete replay result in one transaction; failed/uncertain commit and lost-response retries never create a second committed batch.
- Incomplete committed key records fail closed and cannot silently trigger re-execution; replay does not reconstruct IDs from mutable data.

Owned paths (sequential shared-file extensions are explicit):

```text
app/schemas/payments.py
app/schemas/headers.py
app/schemas/errors.py
app/models/payment.py
app/models/idempotency.py
app/services/payments.py
app/services/idempotency.py
app/main.py
app/api/router.py
app/api/errors.py
app/api/operations.py
app/api/v1/payments.py
migrations/versions/0001_initial.py
app/db/migrate.py
alembic.ini
migrations/env.py
migrations/script.py.mako
tests/test_idempotency.py
scripts/harness_checks.py
tests/conftest.py
tests/__init__.py
tests/test_payments.py
tests/test_journeys.py
tests/features/bulk_payments.feature
```

Registered checks: baseline-syntax, quality, idempotency, local-policy.

## concurrency-and-bounds

Independent instances cannot overspend or duplicate transfers; bounded pool/lock/statement/transaction/operation failures clean up and permit safe retries.

Dependencies: durable-idempotency.

Requirements: FR-3, FR-4, FR-5, FR-6, FR-8, NFR-2, NFR-3, NFR-4.

Invariants: INV-1, INV-2, INV-3, INV-4, INV-5, INV-6.

Scenarios: SC-11, SC-12, SC-13, SC-14, SC-15, SC-16, SC-20.

Acceptance:

- Observe actual overlapping database execution across independent instances; verify distinct-key spending, identical-key races and opposing transfers with durable effects.
- Validate configured C/P/D algebra and the explicitly labeled local profile; invalid/missing production budgets fail startup.
- Verify queue/acquisition/connect/lock/statement/idle/whole-transaction and operation bounds, cleanup, pool recovery and no unbounded retry loop.
- Inject failures around monetary updates and response/commit acknowledgement; distinguish rollback certainty from an unknown commit and always retry the same key.
- Test parameters are justified fixtures, not product capacity requirements; fresh checkouts materialize their own ignored prerequisites.

Owned paths (sequential shared-file extensions are explicit):

```text
app/core/config.py
app/db/database.py
app/services/payments.py
app/services/idempotency.py
app/main.py
app/api/router.py
app/api/errors.py
app/api/operations.py
app/api/v1/payments.py
tests/test_concurrency.py
tests/test_settings.py
tests/test_resource_failures.py
scripts/harness_checks.py
tests/conftest.py
tests/__init__.py
tests/test_payments.py
tests/test_journeys.py
tests/features/bulk_payments.feature
```

Registered checks: baseline-syntax, quality, contention, resource-failures, local-policy.

## observable-operations

Structured logs, correlated traces, bounded metrics and management endpoints distinguish transfer outcomes while telemetry failures never affect money or replay.

Dependencies: concurrency-and-bounds.

Requirements: FR-4, FR-5, NFR-4, NFR-6, NFR-7, NFR-5.

Invariants: INV-1, INV-4, INV-6, INV-2, INV-3.

Scenarios: SC-22, SC-23, SC-24, SC-25, SC-18, SC-21.

Acceptance:

- Bind SC-22–25, including concurrent request-context isolation, matching X-Request-ID, safe W3C traces and all outcome classes.
- Assert redaction using canaries in payloads/identities/keys/credentials/driver errors across logging, spans, framework output and labels.
- Replays never increment newly created-payment counters; metrics are best effort rather than a financial ledger; registries/providers are app-scoped.
- Liveness and metrics do not query the financial database; readiness fails/recoveries are bounded and all probes are non-mutating.
- Full queues/broken sinks/exporters and bounded shutdown preserve committed response and same-key behavior; document operations/runbook/scrape and no invented alert thresholds.
- Own exact locked telemetry dependencies and re-run transitive prerequisite checks after instrumentation touches transaction/pool code.
- Rebuild the final service image and execute SC-18/21 over real HTTP: every returned payment ID/amount/description/order matches persisted rows, expected balances and stable same-key replay.
- Extend the container/Compose/CI and operations deliverables from slice 1 to the complete accepted service, operational routes and all registered checks/scenarios owned by this slice; no pending, undefined or skipped bindings.
- Inspect complete review-input size before clean review; never truncate source or replace missing isolated results with host-only summaries.

Owned paths (sequential shared-file extensions are explicit):

```text
app/core/observability.py
app/core/config.py
app/schemas/payments.py
app/schemas/headers.py
app/schemas/errors.py
app/models/payment.py
app/models/idempotency.py
app/db/database.py
app/services/payments.py
app/services/idempotency.py
app/main.py
app/api/router.py
app/api/errors.py
app/api/operations.py
app/api/v1/payments.py
tests/test_observability.py
tests/test_health.py
scripts/harness_checks.py
pyproject.toml
uv.lock
docs/operations.md
tests/conftest.py
tests/__init__.py
tests/test_payments.py
tests/test_journeys.py
tests/features/bulk_payments.feature
Dockerfile
.dockerignore
compose.yaml
.github/workflows/ci.yaml
.python-version
scripts/check.py
tests/test_container.py
examples/firms.sql
examples/bulk-payment.json
README.md
```

Registered checks: baseline-syntax, quality, observability, container-http, local-policy.

## sanitize-description-text

Strip null/isolated surrogate characters before bounds/fingerprint and persist stable sanitized results.

Dependencies: observable-operations.

Requirements: FR-1, FR-4, FR-5.

Invariants: INV-1, INV-3, INV-4.

Scenarios: SC-27, SC-28.

Acceptance:

- All sanitizer outline rows and supported Unicode preserve exact stable response/payment IDs.

Owned paths (sequential shared-file extensions are explicit):

```text
app/schemas/payments.py
app/services/idempotency.py
tests/test_models.py
tests/test_core.py
tests/test_journeys.py
tests/features/bulk_payments.feature
```

Registered checks: baseline-syntax, local-policy, quality, money-unit, atomic-api.

## async-payment-lifecycle

Own one event-loop operation through commit, cleanup, checkout and return handoff.

Dependencies: sanitize-description-text.

Requirements: FR-8, NFR-2, NFR-3, NFR-7.

Invariants: INV-1, INV-4, INV-5.

Scenarios: SC-14, SC-15, SC-16, SC-20, SC-29.

Acceptance:

- Withheld TCP replies and actual pre/post-handoff cancellation preserve bounded cleanup, caller cancellation and same-key recovery.

Owned paths (sequential shared-file extensions are explicit):

```text
app/db/database.py
app/core/config.py
app/main.py
app/api/operations.py
tests/conftest.py
tests/test_resource_failures.py
tests/test_concurrency.py
tests/test_journeys.py
```

Registered checks: baseline-syntax, local-policy, quality, resource-failures, contention.

## versioned-app-service-dotenv

Serve only /v1/bulk-payments with schema validation, transfer-only service and deterministic dotenv config.

Dependencies: async-payment-lifecycle.

Requirements: FR-1, FR-4, FR-5, FR-8, NFR-4.

Invariants: INV-1, INV-3, INV-4.

Scenarios: SC-21, SC-22, SC-30.

Acceptance:

- Legacy route404 has no effects; payment service owns no HTTP/idempotency/lifecycle; dotenv precedence and explicit mapping isolation hold.

Owned paths (sequential shared-file extensions are explicit):

```text
app/__init__.py
app/api/__init__.py
app/api/errors.py
app/api/operations.py
app/api/router.py
app/api/v1/__init__.py
app/api/v1/payments.py
app/core/__init__.py
app/core/config.py
app/core/observability.py
app/db/__init__.py
app/db/database.py
app/db/migrate.py
app/main.py
app/models/__init__.py
app/models/idempotency.py
app/models/payment.py
app/schemas/__init__.py
app/schemas/errors.py
app/schemas/headers.py
app/schemas/payments.py
app/services/__init__.py
app/services/idempotency.py
app/services/payments.py
.env.example
.gitignore
.dockerignore
Dockerfile
compose.yaml
pyproject.toml
uv.lock
tests/test_settings.py
tests/test_core.py
tests/test_payments.py
tests/test_journeys.py
```

Registered checks: baseline-syntax, local-policy, quality, money-unit, atomic-api, uuid-index, idempotency, contention, resource-failures, observability, container-boot, container-http.

## alembic-container-runtime

A production-only non-root image applies a tracked atomic initial revision to an empty database before HTTP startup.

Dependencies: versioned-app-service-dotenv.

Requirements: FR-10, FR-12, FR-14, NFR-4, NFR-5, NFR-8.

Invariants: INV-7.

Scenarios: SC-26, SC-31, SC-34, SC-36, SC-37.

Acceptance:

- Build actual locked production image with migration resources and dotenv; no app ORM imports.
- Replace standalone DDL bootstrap; initial revision creates exact existing schema and commits head atomically.
- Repeated upgrades preserve data; injected revision failure rolls back schema/version; destructive initial downgrade fails without data changes.
- Until legacy adoption is implemented, reject a nonempty unversioned application schema rather than stamp it blindly.

Owned paths (sequential shared-file extensions are explicit):

```text
alembic.ini
migrations/env.py
migrations/script.py.mako
migrations/versions/0001_initial.py
app/db/migrate.py
app/db/schema.sql
app/core/config.py
pyproject.toml
uv.lock
Dockerfile
compose.yaml
.dockerignore
.github/workflows/ci.yaml
scripts/check.py
scripts/harness_checks.py
tests/test_migrations.py
tests/test_container.py
tests/conftest.py
tests/test_journeys.py
tests/features/bulk_payments.feature
README.md
docs/operations.md
```

Registered checks: baseline-syntax, local-policy, quality, container-boot.

## alembic-safe-adoption

Verified legacy adoption and independent concurrent upgrade callers preserve schema, identities and durable payment results.

Dependencies: alembic-container-runtime.

Requirements: FR-11, FR-12, NFR-8.

Invariants: INV-1, INV-2, INV-3, INV-4, INV-7.

Scenarios: SC-32, SC-33, SC-35.

Acceptance:

- Verify complete legacy schema before adopting; partial/mismatched schema remains unchanged with no false head.
- Compare full rows and sequence identities, and replay retained responses after adoption.
- Observe actual overlapping independent migration execution; advisory locking precedes version inspection and finite wait failure cannot corrupt schema.

Owned paths (sequential shared-file extensions are explicit):

```text
app/db/database.py
app/db/migrate.py
migrations/env.py
migrations/versions/0001_initial.py
tests/test_migrations.py
tests/conftest.py
tests/test_journeys.py
scripts/harness_checks.py
README.md
docs/operations.md
```

Registered checks: baseline-syntax, local-policy, quality, container-boot, atomic-api, idempotency.

## alembic-startup-integration

Unsupported/missing head refuses startup; the migrated production container preserves every prior financial and operational contract.

Dependencies: alembic-safe-adoption.

Requirements: FR-1, FR-2, FR-3, FR-4, FR-5, FR-6, FR-7, FR-8, FR-9, FR-10, FR-11, FR-12, FR-13, FR-14, NFR-1, NFR-2, NFR-3, NFR-4, NFR-5, NFR-6, NFR-7, NFR-8.

Invariants: INV-1, INV-2, INV-3, INV-4, INV-5, INV-6, INV-7.

Scenarios: SC-1, SC-2, SC-3, SC-4, SC-5, SC-6, SC-7, SC-8, SC-9, SC-10, SC-11, SC-12, SC-13, SC-14, SC-15, SC-16, SC-17, SC-18, SC-19, SC-20, SC-21, SC-22, SC-23, SC-24, SC-25, SC-26, SC-27, SC-28, SC-29, SC-30, SC-31, SC-32, SC-33, SC-34, SC-35, SC-36, SC-37, SC-38.

Acceptance:

- Require structure and supported head before serving requests; missing/older/unknown revision fails without monetary effects.
- Execute all38 scenarios/70 cases, all12 candidate-bound groups, real PostgreSQL faults/contention and final production HTTP sample/replay.
- Complete source gets one fresh independent verdict; final acceptance is a separate exact audited decision.

Owned paths (sequential shared-file extensions are explicit):

```text
app/db/database.py
app/main.py
app/api/operations.py
alembic.ini
migrations/env.py
migrations/versions/0001_initial.py
tests/test_migrations.py
tests/test_container.py
tests/test_health.py
tests/test_core.py
tests/test_journeys.py
tests/conftest.py
tests/features/bulk_payments.feature
scripts/check.py
scripts/harness_checks.py
scripts/project_workflow.py
tests/test_project_workflow.py
.github/workflows/ci.yaml
README.md
docs/operations.md
```

Registered checks: baseline-syntax, local-policy, quality, money-unit, atomic-api, uuid-index, idempotency, contention, resource-failures, observability, container-boot, container-http.

## Scenario-to-check coverage

| Registered group | Current acceptance responsibility |
| --- | --- |
| baseline-syntax | Parse app/, migrations/ and scripts/; not functional acceptance. |
| local-policy | Authenticate exact project policy/amendment bindings and reject stale/altered evidence. |
| quality | Locked dependency consistency, formatting/lint and strict application/script/migration typing. |
| money-unit | Pure money grammar/range, canonicalization, settings and dotenv precedence. |
| atomic-api | SC-1–6,14,17,19,21,27,28,30 plus legacy migration SC-32,33,35. |
| uuid-index | SC-17 with representative population and actual query-plan evidence. |
| idempotency | SC-7–10,16 and retained-response compatibility after legacy adoption. |
| contention | SC-11–13 across independent pools with actual database overlap. |
| resource-failures | SC-14–16,20,29 and pre/post-handoff timeout/cancellation recovery. |
| observability | SC-22–25 with async correlation, safe labels, readiness and degraded telemetry. |
| container-boot | SC-26 and migration SC-31,34,36–38 in actual production-only packaging. |
| container-http | SC-18,21 through Linux HTTP/database boundaries after migration. |

All scenario IDs and every outline row must have owned executable bindings. New migration
cases are required work, not pre-existing PASS evidence. All12 registered groups run on the
exact integrated source; isolated inputs create their own prerequisites and missing Docker
fails rather than skips. The approved project-local execution policy replaces native worker
transport, not evidence integrity or independent review.

## Commit and gate progression

Follow docs/harness/COMMIT_POLICY.md. Commit the aligned documentation contract after its
exact audited approval, then each validated migration slice and focused correction. Include
required tests/configuration with source; preserve unrelated staging and exclude secrets,
private approvals and generated evidence. Keep local commits; publication needs authority.

Retain the original three-round repair allowance and its consumed history. The separately
approved supplemental round4 remains scoped to review4's pool-return finding; it is not a
new general repair pool. Each migration task has bounded coordinator-managed execution and
verification, with at least20 percent reserved for checks/handoff. Do not silently reset
attempts, spend an exhausted allowance or infer acceptance from a source checkpoint.

The complete integrated snapshot must fit the consolidated request's explicit static review
source/prompt caps (640000/1310720 bytes) and unchanged900-second reviewer bound; stop if it
does not fit, never truncate. Review remains a fresh no-tools/no-memory full-source verdict.
After current checks and clean review, prepare a separate exact acceptance request. Merge,
push, deployment, release and shared harness changes are outside this amendment.
