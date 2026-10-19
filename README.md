# Firm Payments

Bulk firm payments with Python 3.15, FastAPI, Pydantic and PostgreSQL 17. Database
access uses explicit parameterized SQL, without an ORM.

The implementation provides atomic batches, durable idempotency, complete ordered payment
IDs, indexed UUID lookup, configurable resource bounds and structured observability.
Verification includes the complete approved Gherkin and real PostgreSQL/container HTTP
checks. Formal feature acceptance and external deployment remain separate decisions.

## Run locally

```sh
docker compose up --build
```

Compose waits for PostgreSQL readiness, runs a one-shot repeat-safe schema bootstrap,
then starts the non-root service on http://localhost:8000. The HTTP application shell is
available at `/docs` and `/openapi.json`. Local ports bind to loopback. PostgreSQL data
persists in a named volume. Credentials in Compose are development-only values.
The Python/uv/PostgreSQL image manifests and Python dependency lock are pinned.

## Verify the current milestone

With uv 0.13.1 and Docker available:

```sh
uv sync --locked --all-groups --managed-python
uv run --locked python scripts/check.py
```

Checks cover Python syntax, lock consistency, source formatting/lint, strict
application/script typing, all 38 scenarios/70 expanded Gherkin cases, concurrency/failure
boundaries and real clean-image/Compose/HTTP tests. That test uses a
fresh build context without a virtual environment, unique loopback ports and owned data
volumes; it proves non-root Python 3.15 HTTP startup and bootstrap preservation of seeded
balances. Missing Docker fails the check. Undefined future check groups fail explicitly.
CI runs the complete application checks. Coordinator-only authority tests run separately
against the selected external approval store, which CI/application containers never mount.

The coordinator uses the approved [project-local policy](docs/harness/PROJECT_OVERRIDES.md)
and `scripts/project_workflow.py` for candidate-bound audited evidence. Local approval
records are not required for ordinary CI checks and are never copied into the image.

## Payment design and example

`POST /v1/bulk-payments` requires `Idempotency-Key`; see the [API contract](docs/features/bulk-payments/api.md).
Accepted batches return 201 with count, exact total cents and the complete ordered
`payments` list (`id`, payer/payee UUIDs, cents, description). Validation/business rejection
returns 422, conflicting payer/key reuse 409, and infrastructure failure 503 with
`Retry-After: 1`. An uncertain commit can still have succeeded: retry the **same key**.

Amounts use exact integer cents. PostgreSQL reserves the payer/key, locks all firms in
ascending ID order, checks the entire batch, and commits payments/balances/full replay
body together. Repeated payees retain separate payment rows; credits are aggregated.
Business declines are durable, malformed input reserves no key, and replay preserves IDs.
All participating balance writers must cooperate with the locking protocol.

```sh
docker compose exec -T postgres psql -U firm_payments -d firm_payments < examples/firms.sql
curl -i http://localhost:8000/v1/bulk-payments \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-1' --data-binary @examples/bulk-payment.json
```

The supplied fresh seed yields Pinecrest $36,748.75, Lopez $1,700.75 and Nair $14,050.50.
Seed insertion preserves existing UUID rows rather than resetting prior balances.

## Operations and boundaries

See [local operations](docs/operations.md) for bootstrap, shutdown and recovery. Schema
bootstrap uses an advisory transaction lock and never changes existing balances; it is
not an upgrade migration for an incompatible pre-existing schema. Every additional schema
change requires a versioned data-migration decision.

This take-home trusts the request payer identity. Authentication, external payment rails,
production capacity qualification and deployment are outside scope. All balance writers
must cooperate with the documented database locking protocol. No publication or final
feature acceptance is implied by a passing local check.

## Configuration

Compose explicitly selects `PAYMENTS_PROFILE=local`. Other deployments default to
`configured` and must supply `DATABASE_URL` plus all numeric budget variables listed in
[code design](docs/features/bulk-payments/code-design.md). Names use `PAYMENTS_`, such as
`PAYMENTS_DB_CONNECTION_BUDGET`, `PAYMENTS_MAX_PROCESSES`, `PAYMENTS_POOL_MAX` and
`PAYMENTS_OPERATION_BUDGET_MS`. Invalid budgets fail startup. The local C=8/P=2,
pool-max=4 profile is an illustrative developer setting, not a capacity or latency SLA.

`GET /health/live` and `/metrics` do not query PostgreSQL. `/health/ready` checks pool/DB
availability within its configured probe/SQL bounds. JSON logs use server request IDs and
safe W3C trace correlation; financial payloads, keys, SQL and raw driver errors are omitted.
See [operations](docs/operations.md) for optional bounded OTLP export and recovery.

Payment and readiness database I/O runs asynchronously on the app-owned event loop. Each payment has one absolute asyncio timeout budget from pool acquisition through commit; the configured margin reserves bounded cleanup and response work. Expired operations discard their connection before task cancellation, and commit uncertainty is resolved by replaying the same idempotency key.

Configuration uses `python-dotenv`: copy `.env.example` to `.env` for local startup
(the repository includes an ignored local-only `.env` when none existed). Values in
the process environment override the file. Values are literal, without `${...}`
interpolation. Startup and `python -m app.db.migrate` share the same loader.
Deployment must supply its configured budgets; `.env` and `.env.*` stay out of images
and Git, while `.env.example` is a tracked template.

The service entry point is `app.main:app`. Payment requests use
`POST /v1/bulk-payments`; the old unversioned path returns404. Health and metrics
retain their existing root paths. Pydantic validation lives in `app/schemas`, plain
domain records in `app/models`, and `BulkPaymentService` in `app/services/payments.py`.
Idempotency is a separate service, and `app/db` owns transactions and timeout cleanup.

Schema changes use Alembic revisions. Run `uv run --locked alembic upgrade head`
(or `uv run --locked python -m app.db.migrate`) before service startup. The image
includes `alembic.ini` and `migrations/`; its one-shot Compose migration finishes
before the HTTP service starts. Application SQL remains psycopg with no ORM.
Initial downgrade is intentionally unsupported because it would remove financial data.

A compatible database created by the former bootstrap can be adopted without changing
its financial records or sequence state. Partial or incompatible application schemas
are rejected; do not bypass this with an unconditional `alembic stamp`. Concurrent
upgrades serialize before version inspection and fail on the finite migration lock bound.

Startup validates both application structure and the packaged Alembic head. Missing,
older or unknown revision state prevents the service from serving requests; run the
approved upgrade first. Migration execution never occurs inside a payment handler.
