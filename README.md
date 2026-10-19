# Firm Payments

Bulk payments between firms’ platform balances using Python 3.15, FastAPI, Pydantic
and PostgreSQL 17. Persistence uses async psycopg/parameterized SQL without an application
ORM; schema migrations use Alembic.

## Approach and architecture

Money uses integer US cents. The payer must cover the full batch: all payments and
balance changes commit together or roll back.
A unique UUID index supports lookups; ascending internal-ID row locks serialize competing
transfers across instances and prevent lock-order deadlocks. Repeated payees retain separate
payment rows with aggregated credits. All balance writers must follow this locking protocol.

Pydantic schemas validate and normalize requests. Controllers handle HTTP and orchestrate
services; the payment service checks the total debit and resulting credits before updating
balances and inserting payments in one database transaction. CRUD owns SQL, while the
database layer owns transactions, pooling and cleanup.

| Location | Responsibility |
| --- | --- |
| `app/api/` | Versioned controllers, idempotency middleware, HTTP errors and route metadata |
| `app/schemas/`, `app/models/` | Validation/normalization and domain records |
| `app/services/` | Payment rules and supporting services |
| `app/crud/` | Parameterized SQL; no transaction ownership |
| `app/db/` | Queries, pool, transactions, deadlines and cleanup |
| `app/core/` | Settings, context and observability |
| `migrations/` | Versioned Alembic schema and legacy-data adoption |

Async work has one absolute deadline, cleanup reserve and independent PostgreSQL timeouts.
Pool/waiter limits are bounded; these settings are not a latency SLA.

## Run locally

### Docker Compose

Requires Docker with Compose:

```sh
docker compose up --build
```

Compose waits for PostgreSQL, runs Alembic, then starts the non-root service.
HTTP: `127.0.0.1:8000`; PostgreSQL: `127.0.0.1:5432`.
Open [Swagger UI](http://localhost:8000/docs) or [OpenAPI](http://localhost:8000/openapi.json).
`compose.yaml` supplies development credentials and a persistent database volume.

### Python on the host

With uv (CI: 0.13.1), create `.env` from [`.env.example`](.env.example), preserving existing
settings. Start only PostgreSQL for the host-run service:

```sh
uv sync --locked --all-groups --managed-python
docker compose up -d postgres
uv run --locked python -m app.db.migrate
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log --log-level critical
```

Startup checks connectivity/revision; handlers never migrate. Drain old writers before
upgrades. Incompatible schemas/destructive downgrades are rejected; do not blindly stamp revisions.

## API and contracts

`POST /v1/bulk-payments` accepts JSON and requires `Idempotency-Key`.
Idempotency is supported: retry the same payload/key to avoid duplicate transfers.
Keys are case-sensitive, payer-scoped, and contain 1–128 visible ASCII characters without spaces.

```json
{
  "payer_firm_uuid": "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41",
  "payments": [{
    "amount": "1200.75",
    "payee_firm_uuid": "8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10",
    "description": "Bookkeeping cleanup"
  }]
}
```

- 1–1,000 payments; positive dollar **strings**, at most two decimal places/32 characters,
  fitting 2,147,483,647 cents. Signs, whitespace, exponents and rounding are rejected.
- Descriptions: up to 2,000 characters after removing null bytes and isolated Unicode
  surrogates; other text is preserved. UUIDs/amounts are normalized.
- Unknown fields, invalid UUIDs and self-payments are invalid. Unknown firms, insufficient
  funds or balance overflow reject the whole batch.

| Status | Meaning |
| --- | --- |
| `201` | Successful batch, including an idempotent replay |
| `422` | Invalid input or durable business rejection; no monetary changes |
| `409` | Request already processing or conflicting key reuse |
| `503` | Service unavailable; retry the same payload/key after `Retry-After: 1` |

A `201` body contains `payments_created`, `total_amount_cents` and ordered `payments`:
`id`, `payer_firm_uuid`, `payee_firm_uuid`, `amount_cents`, `description`.
IDs are database-generated.

Optional `X-Correlation-ID`: 1–128 ASCII letters/digits/`._-`; invalid/duplicate values
are replaced. Responses echo it and a fresh `X-Request-ID`, including on replay.

### Try the supplied sample

```sh
docker compose exec -T postgres psql -U firm_payments -d firm_payments < examples/firms.sql
curl -i http://localhost:8000/v1/bulk-payments \
  -H 'Content-Type: application/json' \
  -H 'X-Correlation-ID: sample-job' \
  -H 'Idempotency-Key: example-1' --data-binary @examples/bulk-payment.json
```

Fresh-seed balances after the three-payment sample: Pinecrest $36,748.75, Lopez $1,700.75,
Nair $14,050.50. Seeding preserves existing rows/balances.
[Full API contract and response examples](docs/features/bulk-payments/api.md).

## Configuration and operation

Pydantic `BaseSettings` validates startup configuration. Precedence: explicit settings >
process environment > root `.env` > defaults. Dotenv values are literal (no interpolation);
runtime `.env` files stay outside images/Git.

| Setting | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL connection URL |
| `PAYMENTS_PROFILE` | `local` supplies development budgets; default `configured` requires explicit budgets |
| `PAYMENTS_LOG_LEVEL` | `DEBUG`, `INFO` (default), `WARNING`, `ERROR` or `CRITICAL` |
| `PAYMENTS_OTLP_ENDPOINT` | Optional OTLP HTTP trace receiver |

Other `PAYMENTS_*` settings bound connections, waiters and timeouts: see
[settings](app/core/config.py) and [operations](docs/operations.md). Budget connections across
all app/maintenance processes, including rollout overlap.

- `GET /health/live`: liveness without database I/O.
- `GET /health/ready`: bounded database availability check; `200` or `503`.
- `GET /metrics`: process-local Prometheus metrics, without financial SQL.
- `firm_payments` JSON logs: INFO arrival/completion and classified failures; DEBUG stages.
  Request/correlation/trace context crosses layers. Payloads, keys, credentials, SQL and raw
  errors are excluded. Bounded queues/export expose telemetry drops.

```sh
docker compose ps
docker compose logs -f migrate service
docker compose down
```

`down` preserves database data. Optional bounded request-record maintenance:

```sh
docker compose run --rm --no-deps service /app/.venv/bin/python \
  -m app.db.idempotency_maintenance --batch-size 100 --max-batches 10 --timeout-seconds 10
```

Maintenance never executes payments; unresolved corruption returns a nonzero exit.

## Tests

With dev dependencies installed and Docker running:

```sh
uv run --locked python scripts/check.py
```

CI runs lock/Ruff/mypy checks, unit tests, PostgreSQL Testcontainers tests and isolated
Compose/HTTP tests. Coverage includes actual contention, atomicity, idempotency/recovery/TTL,
migrations, cancellation/failures and telemetry. Missing Docker fails verification.

Focused examples:

```sh
uv run --locked pytest -q tests/test_models.py
uv run --locked pytest -q tests/test_idempotency.py
uv run --locked pytest -q tests/test_journeys.py --scenarios=SC-21
```

The [Gherkin contract](tests/features/bulk_payments.feature) has 60 scenarios / 106 cases.
Harness authority tests run separately.

## Scope and known issues

Payer identity is trusted; authentication, external payment rails and production qualification
are outside this take-home. Deferred issues:

- Cancellation arriving during cleanup can be suppressed.
- Failed result publication can undercount acknowledged payments in metrics.
- The phase-retention cap can omit the main business transaction timing.

One earlier clean-image check reported a missing `payments` table; subsequent checks passed,
but its cause remains unresolved. Metrics are best effort, not the financial ledger.
