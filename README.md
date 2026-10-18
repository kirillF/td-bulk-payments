# Firm Payments

A take-home backend service for atomic, idempotent bulk payments between accounting firms.

## Foundation

Python 3.15, FastAPI, Pydantic v2, PostgreSQL 17 and psycopg 3 with explicit SQL.
This initial foundation contains an ASGI shell and development tooling. Payment routes,
schema migrations and behavioral tests are subsequent implementation work.

```sh
uv sync --locked --all-groups --managed-python
uv run --locked python scripts/check.py
uv run --locked uvicorn firm_payments.app:app --reload
# Alternatively, build the service and start local PostgreSQL:
docker compose up --build
```

Compose credentials are local development values. The database port binds to loopback.
Interactive API documentation is at http://localhost:8000/docs.
Testcontainers integration tests will use independent PostgreSQL containers and require Docker.

## Agreed behavior for implementation

`POST /bulk-payments` requires an `Idempotency-Key` header scoped to the payer.
Valid batches commit payments and all balance changes together and return 201.
Insufficient funds returns 422 with no payment or balance changes.
A third table records normalized request fingerprints and durable responses, including
insufficient-funds responses. Identical retries replay the original response; changed
content under the same payer/key returns 409. Database failures roll back and permit retry.
Firm UUIDs have a unique index. Transactions lock participating firms in internal-ID order.
Amounts are positive decimal strings converted exactly to cents, with overflow checks.
Empty batches, unknown firms and self-payments return 422; repeated recipients are allowed.

Authentication and external deployment are outside this take-home. A production host must
authorize the payer against the authenticated caller. Idempotency retention is durable;
no automatic expiry or cleanup is implied. Container image tags and CI action major tags
can receive upstream updates; Python dependencies are locked exactly in uv.lock.

## Source map

- `firm_payments/app.py`: FastAPI application shell.
- `scripts/check.py`: formatting, lint, typing and lock consistency checks.
- `compose.yaml` and `Dockerfile`: local database and containerized service.
- `docs/harness/FOUNDATION.md`: foundation architecture and execution constraints.

Local setup selects uv-managed Python 3.15 to avoid depending on the host interpreter.
See `docs/FOUNDATION_DESIGN.md` for installation scope, risks and approval boundaries.
