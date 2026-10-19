# Firm Payments — API contract

Implements [FR-1–14 and NFR-1–8](requirements.md) and [SC-1–38](scenarios.feature) under the accepted
[architecture](architecture.md). This is the proposed program contract, not a claim of
implementation completion.

## HTTP request

```text
POST /v1/bulk-payments
Content-Type: application/json
Idempotency-Key: <case-sensitive operation key>

BulkPayment {
  payer_firm_uuid: UUID string,
  payments: Payment[1..1000]
}
Payment {
  amount: string,
  payee_firm_uuid: UUID string,
  description: string[0..2000 characters]
}
```

Exactly one nonempty key is required: 1–128 visible ASCII characters, without spaces.
Multiple key headers are ambiguous and return 422. Unknown JSON attributes, malformed
JSON/UUIDs, missing fields, numeric money values and self-payments return 422 before any
key reservation. Missing/non-JSON media type need not be accepted as a valid JSON request.

Amount grammar is ASCII digits followed optionally by `.` and one or two digits; maximum
length is 32. Leading zeroes are valid; numeric value must be positive and at most
2,147,483,647 cents. No whitespace, signs, exponents, rounding or floating point. Description
text is sanitized by removing U+0000 and isolated surrogate code points U+D800–U+DFFF before applying its length bound and fingerprinting. This sanitization does not produce a validation error; empty sanitized text is valid. Preserve all other Unicode and whitespace exactly. Persist and return the sanitized text. Requests differing only by removed characters have the same fingerprint and replay under the same key; distinct keys remain independent. UUID spelling is normalized
to its canonical lowercase representation.

The exact SQL seed and request in SC-21 are normative examples, including repeated Nair
entries and the `6250`, `5800.5`, `1200.75` amount spellings.

## Responses

| Status | JSON body | Reservation/effects |
| --- | --- | --- |
| 201 | `BulkPaymentResponse`: `payments_created`, `total_amount_cents`, and the full `payments` list specified below. | All batch effects and this result committed, or this result replayed with the same payment IDs. |
| 422 | FastAPI-compatible validation body with a `detail` array. | Invalid request; no reservation or monetary changes. |
| 422 | `{"detail": "unknown_firm"}` | Durable business rejection; no monetary changes. |
| 422 | `{"detail": "insufficient_funds"}` | Durable business rejection; no monetary changes. |
| 422 | `{"detail": "balance_limit_exceeded"}` | Durable business rejection; no monetary changes. |
| 409 | `{"detail": "idempotency_key_conflict"}` | Original result remains unchanged; no additional effects. |
| 503 | `{"detail": "service_unavailable"}` and `Retry-After: 1`. | Retryable infrastructure failure; commit may be uncertain, so retry the same key. |

Validation error wording is framework-generated and not a stable machine error code.
Do not expose SQL, credentials, stack traces or raw database exceptions in HTTP bodies.
Unexpected corrupt/incomplete durable request state also fails closed with 503 and an
internal diagnostic; it never authorizes a second execution.

Each success response contains one payment object per request entry, in request order:

```json
{
  "payments_created": 1,
  "total_amount_cents": 625000,
  "payments": [
    {
      "id": 42,
      "payer_firm_uuid": "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41",
      "payee_firm_uuid": "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25",
      "amount_cents": 625000,
      "description": "Overflow returns, August 2026"
    }
  ]
}
```

The example ID is illustrative, not a reserved or gapless value. IDs are positive integers
assigned by PostgreSQL to the persisted payment rows. Use public firm UUIDs in the response,
not internal firm IDs. `amount_cents` is the exact persisted integer value. Repeated payees
produce distinct payment objects and IDs. `payments_created` equals the list length and
`total_amount_cents` equals the sum of its amounts. SC-21 returns three such objects with
the supplied amounts/descriptions and their actual generated IDs.

OpenAPI documents the payment objects, success fields and both validation/business error
shapes, including 409 and 503. No batch ID, status polling, authentication or history
endpoint is added. Error responses do not contain partially created payment objects.

## Correlation and operational endpoints

Each request receives a server-generated UUID request ID. Return it in `X-Request-ID`
on successful and error responses, including validation failures, and use the same value
in that attempt's logs. Do not trust or reflect an arbitrary incoming request ID. A retry
gets a new request ID while retaining its original payment result/IDs.

Accept valid W3C trace context through the tracing SDK; ignore malformed context and do
not propagate arbitrary baggage. Correlate request logs with trace/span IDs when present.
No financial payload, idempotency key, authorization header or database URL becomes a
trace attribute. Correlation headers are transport metadata, not part of the durable
idempotency result.

| Endpoint | Contract |
| --- | --- |
| `GET /health/live` | 200 with `{"status":"alive"}` while the process serves requests; no database call or monetary effects. Database outage must not make liveness fail. |
| `GET /health/ready` | 200 with `{"status":"ready"}` only after initialization/schema compatibility checks and a bounded read-only database/pool probe; otherwise 503 with `{"status":"not_ready"}`. Never expose connection/error details or reserve a key. |
| `GET /metrics` | Prometheus exposition from process-local metrics/registry, without a database query; never a balance or payment-data export. |

Management routes are operational, not additional payment APIs. Restrict them to trusted
monitoring access through deployment networking/ingress; Compose remains loopback-only.
Scrape each single-worker service process separately and aggregate in the monitoring
system. Multiple app objects in tests must have separate registries; multiple worker
processes behind one unspecific scrape target are unsupported without an explicit
aggregation configuration. Routine probes/scrapes do not produce payment completion logs
or count as payment traffic.

## Replay and compatibility

The key namespace is `(canonical payer UUID, exact key string)`. Fingerprint v1 is SHA256
of UTF-8 bytes consisting of `v1:` followed by deterministic JSON with sorted object keys,
compact separators and literal Unicode (`ensure_ascii=False`). Its document has exactly
`payer_firm_uuid` and `payments`; each payment has `amount`, `payee_firm_uuid`, `description`.
Canonical amounts are dollar strings with exactly two fractional digits, derived with
integer arithmetic. Payment array order and descriptions remain unchanged.

The full success list, including every generated payment ID and its request-order position,
is stored in the idempotency result in the same transaction as the payment rows. Replay
returns those same IDs and values; it does not rebuild the list from mutable firm data.
Replay preserves the original HTTP status and JSON value; transport headers and object
serialization order are not byte-for-byte delivery guarantees. Normalized equivalent
money/UUID spellings replay; a changed amount, recipient, description or entry order returns
409. Business 422 results remain stable after later deposits or firm creation. Invalid
requests do not consume keys. There is no automatic expiry.

Any future change to fingerprint format, response semantics or key retention requires an
explicit compatibility/migration decision. Deployments must continue to interpret retained
v1 records; do not reinterpret old keys under a new canonicalization algorithm.

## Application interfaces and dependency direction

Schemas in app/schemas/payments.py define Payment, BulkPayment, PaymentCreated and
BulkPaymentResponse with existing bounds/validators. app/schemas/headers.py owns key value
validation; controller extraction enforces exactly one header. Error schemas are separate.
Plain model records have no HTTP status field:

```python
@dataclass(frozen=True)
class TransferResult:
    kind: Literal["accepted", "rejected"]
    body: dict[str, Any]

@dataclass(frozen=True)
class Result:
    kind: Literal["accepted", "rejected", "conflict"]
    body: dict[str, Any]
    outcome: Literal["committed", "replayed", "business_rejected", "conflict"]

class BulkPaymentService:
    @staticmethod
    async def transfer(connection: AsyncConnection[DictRow],
                       request: BulkPayment) -> TransferResult: ...

# app/services/idempotency.py: operates inside its caller-owned transaction
async def execute(connection: AsyncConnection[DictRow],
                  request: BulkPayment, key: str) -> Result: ...

# app/db/database.py: owns checkout, deadline and transaction completion
async def run_transaction[T](pool: Pool,
    work: Callable[[AsyncConnection[DictRow]], Awaitable[T]]) -> T: ...

class Settings:
    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Self: ...
    @classmethod
    def local(cls, database_url: str, **overrides: int) -> Self: ...

def load_environment(env: Mapping[str, str] | None = None) -> dict[str, str]: ...
def create_app(configuration: Settings | str | None = None) -> FastAPI: ...
def create_pool(configuration: Settings | str) -> Pool: ...
def migrate(database_url: str) -> None: ...
```

The controller awaits run_transaction(pool, callback), with callback invoking the separate
idempotency service. Only after that await succeeds does it map accepted/rejected/conflict
to201/422/409. Result.outcome is attempt telemetry, not persisted response data. The storage
codec retains existing201/422 status values for durable compatibility. BulkPaymentService
returns a transfer result; commit ownership and HTTP translation stay in their layers.

Settings load during lifespan, not import. Explicit test settings/mappings bypass ambient
configuration. Application startup must validate both schema and supported revision; an
invalid configuration, missing head or unavailable prerequisite leaves no serving app.

## Async lifetime and failure boundary

One absolute asyncio.timeout_at operation deadline covers acquisition, checkout validation,
reservation, transfer and commit. Expiry becomes503 with Retry-After:1 for a connected caller;
COMMIT overlap remains uncertain. Before pool handoff the owner closes/discards its borrowed
session before cancelling/joining the child. After synchronous ownership transfer to pool
maintenance it must not close that session. No detached financial task continues; nonfinancial
pool maintenance owns subsequent capacity recovery. External cancellation propagates after
bounded cleanup rather than manufacturing an HTTP response.

## Migration command contract

`python -m app.db.migrate` reads DATABASE_URL through the shared dotenv/environment loader
and invokes Alembic upgrade head. `alembic upgrade head` is the equivalent direct migration
command, using alembic.ini and migrations/env.py. The application entry point is app.main:app.
Successful upgrades exit0 with the head recorded; incompatible/partial legacy schemas,
unsupported destructive downgrade or migration failure exit nonzero without falsely advancing
the revision. Runtime errors are sanitized; credentials and raw SQL never reach CLI output.
Migration execution uses bounded SQLAlchemy Core/psycopg connections and a single advisory-
locked transaction. Configuration in alembic.ini contains resource locations, not credentials.

Old POST /bulk-payments returns404 without reservation/effects; the sole payment route is
POST /v1/bulk-payments. Health and metrics remain unversioned. Startup schema/revision failure
is an initialization error, not a new payment-response shape or a payment migration endpoint.

## Configuration

Read repository-root .env as literal defaults with python-dotenv and overlay os.environ;
explicit mappings bypass both. Do not mutate process environment or load at import time.
Missing .env is valid if runtime environment supplies settings. .env.example contains local-
only illustrative values; actual dotenv files are excluded from Git and images. Keep all
configured-profile validation and existing operational limits; migration commands have their
own finite bounds. New migration framework dependencies are locked without unrelated upgrades.
