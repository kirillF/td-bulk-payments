# Firm Payments

Bulk firm payments with Python 3.15, FastAPI, Pydantic and PostgreSQL 17. Database
access uses explicit parameterized SQL, without an ORM.

The implementation provides atomic batches, durable idempotency, complete ordered payment
IDs, indexed UUID lookup, configurable resource bounds and structured observability.
Verification uses executable Gherkin and real PostgreSQL/container HTTP checks.
The executable contract contains60 scenarios/106 expanded cases. Candidate-bound checks and independent review establish verification status; formal feature acceptance and external deployment remain separate decisions.

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
application/script typing, the registered requirement-linked Gherkin cases, concurrency/failure
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
returns 422, processing or conflicting payer/key reuse 409, and infrastructure failure 503 with
`Retry-After: 1`. An uncertain commit can still have succeeded: retry the **same key**.

Amounts use exact integer cents. Middleware commits a processing claim before controller
admission. The controller locks firms in ascending ID order and atomically commits
payments, balances and a result receipt. Middleware then publishes the completed result
in a separate transaction. Duplicates return processing409 until publication or bounded
receipt recovery, then replay the same result. One pool serves successive checkouts.
Repeated payees retain separate payment rows; credits are aggregated.
Business declines are durable, malformed input reserves no key, and replay preserves IDs.
All participating balance writers must cooperate with the locking protocol.

```sh
docker compose exec -T postgres psql -U firm_payments -d firm_payments < examples/firms.sql
curl -i http://localhost:8000/v1/bulk-payments \
  -H 'Content-Type: application/json' \
  -H 'X-Correlation-ID: sample-job' \
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

Configuration is validated by `pydantic-settings.BaseSettings`, with literal `python-dotenv` defaults: copy `.env.example` to `.env` for local startup
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

Startup checks connectivity and the packaged Alembic head; migrations own schema integrity. Missing,
older or unknown revision state prevents the service from serving requests; run the
approved upgrade first. Migration execution never occurs inside a payment handler.


Settings load once during application startup. Explicit settings and test mappings are
isolated from ambient environment/dotenv; normal constructor values take precedence over
process environment, which takes precedence over the literal local file. Unknown log
levels, invalid numeric fields and inconsistent budgets fail safely before serving.
`PAYMENTS_LOG_LEVEL` accepts DEBUG, INFO (default), WARNING, ERROR or CRITICAL.

`PAYMENTS_POOL_MAX` bounds one pool per process, serving successive lifecycle and
business transactions. Physical connections may be reused; no lifecycle partition setting
is required. Native queue limits apply to every checkout. Include every process and
maintenance command in the deployment connection budget. Startup does not detect manual
schema drift with an unchanged Alembic revision; manage schema through migrations.

Operational logs use the dedicated `firm_payments` JSON logger. `X-Correlation-ID` accepts
one 1–128 character ASCII letters/digits/dot/underscore/hyphen token; malformed or duplicate
values are replaced. Every application HTTP response returns it and a fresh `X-Request-ID`.
`PAYMENTS_LOG_LEVEL=INFO` records request arrival/status and classified failures; `DEBUG`
adds bounded stage and insert-ordinal events. Financial payloads and raw errors are excluded.

Idempotency responses are retained for exactly24 hours from completed publication using
PostgreSQL time; replay does not extend expiry. A safely expired key can execute a new
payment. Unresolved/active processing entries cannot be reclaimed from age alone.
For bounded receipt reconciliation and expired-key cleanup, run explicitly:

```sh
python -m app.db.idempotency_maintenance --batch-size 100 --max-batches 10 --timeout-seconds 10
```

The command never executes payments. It publishes committed receipts, fences abandoned
claims with no effects, skips busy guards and retains corrupt records with a nonzero exit.
Its numeric arguments are operator-selected bounds, not throughput guarantees.
