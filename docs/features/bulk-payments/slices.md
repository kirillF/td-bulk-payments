# Firm Payments — current slices

Seven sequential slices. Migration and typed-settings deliveries remain historical local
evidence; the approved single-pool/Alembic amendment supersedes conflicting target choices.
All prior approvals, failures, deadlines and counters remain immutable. The simplification
is a focused correction; existing per-slice caps remain unchanged. Container delivery was first.

## lifecycle-container-migration

Non-root production image applies new revision atomically, preserves old results and verifies new supported structure/head. No payment traffic is served from this intermediate candidate.

Dependencies: existing delivered-local Alembic baseline; no new-slice dependency.

Requirements: FR-10, FR-11, FR-12, FR-13, FR-14, FR-19, NFR-5, NFR-8.

Invariants: INV-1, INV-2, INV-3, INV-4, INV-5, INV-6, INV-7, INV-8.

Scenarios: SC-31, SC-32, SC-33, SC-34, SC-35, SC-36, SC-37, SC-38, SC-55.

Acceptance:

- Preserve original migrations; new exact head/structure, guard/receipt FK/check/index definitions and full24h legacy cutover.
- Refuse invalid/incomplete old results without schema/revision/data changes; no silent repair or destructive downgrade.
- Real container upgrade/repeat/concurrency/failure rollback checks; independent copied data/sequences remain equal.
- Authenticate exact new amendment and chained prior approvals in the local workflow adapter before revised checks.
- Intermediate schema image is private; do not claim old payment writers compatible or deploy mixed versions.

Owned paths (shared extensions are sequential):

```text
scripts/project_workflow.py
tests/test_project_workflow.py
migrations/versions/0002_request_lifecycle.py
app/db/database.py
app/db/migrate.py
app/schemas/idempotency.py
app/models/idempotency.py
Dockerfile
compose.yaml
tests/test_migrations.py
tests/test_container.py
README.md
docs/operations.md
scripts/harness_checks.py
tests/test_journeys.py
tests/features/bulk_payments.feature
tests/conftest.py
```

Registered checks: baseline-syntax, local-policy, quality, container-boot.

## typed-settings

Database, logging and telemetry settings are typed BaseSettings with compatible explicit source semantics.

Dependencies: lifecycle-container-migration.

Requirements: NFR-3, NFR-4, NFR-11.

Invariants: INV-1, INV-2, INV-3, INV-4, INV-5, INV-6, INV-7, INV-8.

Scenarios: SC-20, SC-45, SC-46.

Acceptance:

- Resolve and lock Python3.15-compatible pydantic-settings without unrelated upgrades.
- Configured budgets remain required; integer/bool handling, algebra, literal dotenv, mapping isolation and environment precedence preserved.
- Typed pool budgets remain bounded; the following simplification supersedes the originally delivered two-pool partition.
- Telemetry consumes injected settings; safe startup failure never dumps URL or raw ValidationError input.

Owned paths (shared extensions are sequential):

```text
app/core/config.py
app/db/database.py
app/db/idempotency_maintenance.py
tests/test_resource_failures.py
app/core/observability.py
app/main.py
app/db/migrate.py
migrations/env.py
pyproject.toml
uv.lock
.env.example
tests/test_settings.py
tests/test_observability.py
tests/test_container.py
README.md
docs/operations.md
scripts/harness_checks.py
tests/test_journeys.py
tests/features/bulk_payments.feature
tests/conftest.py
```

Registered checks: baseline-syntax, local-policy, quality, money-unit, container-boot.

## single-pool-alembic-simplification

One bounded pool serves successive independent transactions; Alembic owns schema structure and startup checks connectivity and supported revision.

Dependencies: typed-settings.

Scenarios: SC-20, SC-45, SC-46, SC-58.

Acceptance:

- One native bounded pool supports pool_max=1; no partition setting, custom admission counter or peer aggregation.
- Separate successive lifecycle/business/publication checkouts may reuse physical sessions; safe cancellation and transaction handling remain.
- Startup checks connectivity and supported revision only; frozen legacy verification is migration-only; committed migration revisions remain byte-identical.
- Real PostgreSQL queue cancellation/recovery, migration rollback/legacy adoption and integrity constraints remain verified.

Owned paths:

```text
app/db/database.py
app/main.py
app/core/config.py
app/core/observability.py
migrations/__init__.py
migrations/legacy_schema.py
migrations/env.py
tests/test_settings.py
tests/test_resource_failures.py
tests/test_migrations.py
tests/test_journeys.py
tests/features/bulk_payments.feature
scripts/harness_checks.py
scripts/project_workflow.py
tests/test_project_workflow.py
.env.example
README.md
docs/operations.md
docs/features/bulk-payments/feature.json
docs/features/bulk-payments/requirements.md
docs/features/bulk-payments/architecture.md
docs/features/bulk-payments/api.md
docs/features/bulk-payments/slices.md
docs/features/bulk-payments/code-design.md
docs/features/bulk-payments/scenarios.feature
```

Registered checks: baseline-syntax, local-policy, quality, money-unit, container-boot.


## middleware-controller-idempotency

Middleware admits/replays/conflicts; controller orchestrates independent payment service plus atomic B receipt and A publication.

Dependencies: single-pool-alembic-simplification.

Requirements: FR-1, FR-4, FR-5, FR-6, FR-7, FR-8, FR-18, FR-19, NFR-2, NFR-3.

Invariants: INV-1, INV-2, INV-3, INV-4, INV-5, INV-6, INV-7, INV-8.

Scenarios: SC-7, SC-8, SC-9, SC-10, SC-11, SC-14, SC-15, SC-16, SC-48, SC-49, SC-50, SC-57.

Acceptance:

- No checker/payment cross-import/callback; controller controls monetary use-case sequence; database owns transaction lifetimes.
- Processing is committed before B, duplicates observe409; completed201/422 replay retains exact ordered IDs; invalid requests never claim.
- Separate sequential A/B/A sessions share absolute deadline and pool; no A row lock held across B.
- Guard verifies current generation/owner before firm locks, receipt commits atomically, publication conditional and validated.
- Crash/failure leaves safe recoverable state, never status-only duplicate execution.

Owned paths (shared extensions are sequential):

```text
app/api/middleware/__init__.py
app/api/middleware/idempotency.py
app/api/v1/payments.py
app/api/errors.py
app/main.py
app/models/idempotency.py
app/schemas/idempotency.py
app/schemas/payments.py
app/schemas/headers.py
app/services/idempotency.py
app/services/payments.py
app/db/idempotency.py
app/db/database.py
tests/test_health.py
docs/features/bulk-payments/feature.json
docs/features/bulk-payments/slices.md
tests/test_migrations.py
tests/test_idempotency.py
tests/test_payments.py
tests/test_core.py
tests/test_concurrency.py
tests/test_resource_failures.py
tests/test_observability.py
README.md
docs/operations.md
scripts/harness_checks.py
tests/test_journeys.py
tests/features/bulk_payments.feature
tests/conftest.py
```

Registered checks: baseline-syntax, local-policy, quality, atomic-api, idempotency, contention, resource-failures, observability.

## correlated-structured-logging

Safe X-Correlation-ID and dedicated leveled JSON logger cover HTTP, use-case and storage operations.

Dependencies: middleware-controller-idempotency.

Requirements: FR-15, FR-16, FR-17, NFR-6, NFR-7, NFR-9, NFR-10, NFR-12.

Invariants: INV-1, INV-2, INV-3, INV-4, INV-5, INV-6, INV-7, INV-8.

Scenarios: SC-39, SC-40, SC-41, SC-42, SC-43, SC-44, SC-47.

Acceptance:

- Header grammar/fallback/cardinality and all application response statuses; current metadata independent of durable result.
- INFO arrival/completion, finite WARNING/ERROR failures, DEBUG all meaningful stages and ordinal-only insert progress.
- Redaction at all levels, isolated contexts including cancellation/cleanup, app-owned handlers, bounded sink/export failure and threshold behavior.

Owned paths (shared extensions are sequential):

```text
app/api/middleware/idempotency.py
docs/features/bulk-payments/feature.json
docs/features/bulk-payments/slices.md
app/core/logging.py
app/core/request_context.py
app/core/observability.py
app/schemas/headers.py
app/main.py
app/api/errors.py
app/api/operations.py
app/api/v1/payments.py
app/services/payments.py
app/services/idempotency.py
app/db/database.py
app/db/idempotency.py
tests/test_settings.py
tests/test_observability.py
tests/test_health.py
tests/test_payments.py
README.md
docs/operations.md
scripts/harness_checks.py
tests/test_journeys.py
tests/features/bulk_payments.feature
tests/conftest.py
```

Registered checks: baseline-syntax, local-policy, quality, observability.

## fenced-recovery-ttl

Finite maintenance plus conditional publication closes the dual-write gap and enforces safe24h generation reuse.

Dependencies: correlated-structured-logging.

Requirements: FR-5, FR-7, FR-8, FR-19, NFR-3, NFR-9.

Invariants: INV-1, INV-2, INV-3, INV-4, INV-5, INV-6, INV-7, INV-8.

Scenarios: SC-14, SC-15, SC-16, SC-49, SC-51, SC-52, SC-53, SC-54, SC-56, SC-57.

Acceptance:

- Bounded CLI candidate/batch/total-time, no hidden scheduler or infinite retry; include maintenance process in deployment budget.
- Hold guard during recovery, validate receipt or fence no-effect abandoned owner; old B cannot recreate deleted guard or execute.
- Before/at/after TTL, no cache extension on replay, completed key can pay anew only after safe generation release.
- Races with active/uncertain B return409; corrupted records retained with safe failure; no financial work in maintenance.
- Actual PostgreSQL fault/overlap observations, owner resumption and A/B crash boundaries preserve all money invariants.

Owned paths (shared extensions are sequential):

```text
app/db/idempotency_maintenance.py
app/db/idempotency.py
app/db/database.py
app/api/middleware/idempotency.py
app/services/idempotency.py
app/models/idempotency.py
app/core/observability.py
app/core/logging.py
tests/test_idempotency.py
tests/test_resource_failures.py
tests/test_concurrency.py
tests/test_observability.py
README.md
docs/operations.md
scripts/harness_checks.py
tests/test_journeys.py
tests/features/bulk_payments.feature
tests/conftest.py
```

Registered checks: baseline-syntax, local-policy, quality, idempotency, contention, resource-failures, observability.

## lifecycle-full-integration

Complete candidate-bound regression and container HTTP evidence before fresh complete-source review and separately audited acceptance.

Dependencies: fenced-recovery-ttl.

Requirements: FR-1, FR-2, FR-3, FR-4, FR-5, FR-6, FR-7, FR-8, FR-9, FR-10, FR-11, FR-12, FR-13, FR-14, FR-15, FR-16, FR-17, FR-18, FR-19, NFR-1, NFR-2, NFR-3, NFR-4, NFR-5, NFR-6, NFR-7, NFR-8, NFR-9, NFR-10, NFR-11, NFR-12.

Invariants: INV-1, INV-2, INV-3, INV-4, INV-5, INV-6, INV-7, INV-8.

Scenarios: SC-1, SC-2, SC-3, SC-4, SC-5, SC-6, SC-7, SC-8, SC-9, SC-10, SC-11, SC-12, SC-13, SC-14, SC-15, SC-16, SC-17, SC-18, SC-19, SC-20, SC-21, SC-22, SC-23, SC-24, SC-25, SC-26, SC-27, SC-28, SC-29, SC-30, SC-31, SC-32, SC-33, SC-34, SC-35, SC-36, SC-37, SC-38, SC-39, SC-40, SC-41, SC-42, SC-43, SC-44, SC-45, SC-46, SC-47, SC-48, SC-49, SC-50, SC-51, SC-52, SC-53, SC-54, SC-55, SC-56, SC-57, SC-58.

Acceptance:

- Bind all58 scenarios and every98 expanded case; changed old idempotency semantics must be deliberate, not a removed check.
- All12 groups on exact source/environment, genuine Docker/Testcontainers contention/faults, sequence gap mutant, money sample and real Linux HTTP status/header/cache/TTL flow.
- Candidate-bound full-source review fits existing640000/1310720 caps and900-second bound; preflight fails closed, no source truncation.
- Existing .env.example review alias request is still unapproved and bound to the older source. Prepare fresh exact representation exception if necessary; no inferred review permission.
- No final acceptance, publication, shared-harness edit or historical repair/attempt counter reset; record limitations and gated next action.

Owned paths (shared extensions are sequential):

```text
.env.example
Dockerfile
README.md
app/api/errors.py
app/api/middleware/__init__.py
app/api/middleware/idempotency.py
app/api/operations.py
app/api/v1/payments.py
app/core/config.py
app/core/logging.py
app/core/observability.py
app/core/request_context.py
app/db/database.py
app/db/idempotency.py
app/db/idempotency_maintenance.py
app/db/migrate.py
app/main.py
app/models/idempotency.py
app/schemas/headers.py
app/schemas/idempotency.py
app/schemas/payments.py
app/services/idempotency.py
app/services/payments.py
compose.yaml
docs/operations.md
migrations/env.py
migrations/versions/0002_request_lifecycle.py
pyproject.toml
scripts/harness_checks.py
tests/conftest.py
tests/features/bulk_payments.feature
tests/test_concurrency.py
tests/test_container.py
tests/test_core.py
tests/test_health.py
tests/test_idempotency.py
tests/test_journeys.py
tests/test_migrations.py
tests/test_observability.py
tests/test_payments.py
tests/test_resource_failures.py
tests/test_settings.py
uv.lock
scripts/project_workflow.py
tests/test_project_workflow.py
scripts/check.py
.github/workflows/ci.yaml
tests/test_index.py
tests/test_models.py
examples/firms.sql
examples/bulk-payment.json
alembic.ini
```

Registered checks: baseline-syntax, local-policy, quality, container-boot, money-unit, atomic-api, uuid-index, idempotency, contention, resource-failures, observability, container-http.
