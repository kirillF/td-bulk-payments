# Firm Payments — API contract

Implements [FR-1–19 and NFR-1–12](requirements.md) and [SC-1–60](scenarios.feature) under the accepted
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
| 409 | `{"detail": "request_processing"}` | Matching request entry is processing; no controller/business execution. Retry the same key later. |
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

## Correlation and visibility

Optional X-Correlation-ID: exactly one case-insensitive header name, case-sensitive opaque
value with 1–128 ASCII letters/digits/dot/underscore/hyphen. Preserve a valid value; replace
absent, empty, malformed, oversized, duplicate or comma-combined values with a UUID hex ID.
Replacement never causes payment rejection and never logs rejected raw metadata. Echo
exactly one effective X-Correlation-ID and a fresh server-generated X-Request-ID on every
application-generated HTTP response, including cached, validation, conflict, 404 and safe
500/503 responses. Disconnected/pre-application requests cannot be promised headers.

Use valid W3C trace context without arbitrary baggage. Correlation/request IDs are not
idempotency keys, payment attributes, fingerprint fields, durable response-body data or
metric labels. Every replay uses current-attempt diagnostic headers and original payment IDs.

INFO arrival/completion logs cover every application HTTP route; DEBUG adds sanitized
processing stages through storage. PAYMENTS_LOG_LEVEL controls threshold. Full redaction
and bounded best-effort delivery apply at every level; no financial payload or raw errors.

## Idempotency lifecycle and TTL

Route-scoped middleware validates and normalizes the body/key through schemas before claim.
Only valid POST /v1/bulk-payments requests create entries; health, unknown paths, wrong
methods and invalid input cannot reserve keys. Replay validation precedes returning any
cached body. Preserve fingerprint v1 and payer/key scope; no change to amount/UUID/description
normalization or payment ordering. Prepared body is replayed safely downstream, not consumed
or inconsistently parsed twice. Admission state is private server state, not trusted headers.

On connection A, an atomic new claim commits processing before admission. Matching completed
entries replay status/body; matching processing entries return 409 request_processing.
Changed normalized content under an unexpired key returns 409 idempotency_key_conflict.
Do not wait for connection B's financial transaction just to replay a concurrent request.
A short simultaneous A claim can contend within existing bounds; database outage remains503.

The controller uses a separate B transaction for monetary logic plus its validated immutable
result receipt. A final A transaction publishes completed from that receipt. No A transaction
is held across B. Return normal201/422 only after publication; failures return safe503 when
possible. Even if publication fails, B may have committed. Recovery must publish the same
receipt and IDs, never execute payments again based solely on a processing flag or timeout.
A definitively abandoned no-effect generation can be released only after fenced recovery.

Processing expiry: created_at+24h. Completed response expiry: first completed publication+24h.
Use database time; replay does not extend retention. Resolved expired key reuse starts a
new generation and can create a new payment. Active/unresolved entries cannot be discarded
by TTL; duplicates can remain409 until bounded reconciliation establishes the result.
Business422 is retained exactly like201; validation422, processing409 and transient503 are
not cached completed results. No new polling endpoint or failed state is introduced.

Existing completed keys receive a full24h window from migration cutover. Preserve fingerprint
version and cached semantics; don't expire old keys immediately or reinterpret their contents.
Corrupt/incomplete durable outcomes fail closed503 and never authorize payment execution.

## Operational endpoints and maintenance

| Endpoint | Contract |
| --- | --- |
| GET /health/live | 200 {"status":"alive"}, no database work; database outage does not break liveness. |
| GET /health/ready | 200 {"status":"ready"} after initialized supported schema and bounded read-only availability check; otherwise503 {"status":"not_ready"}. |
| GET /metrics | Process-local finite-label Prometheus metrics without financial SQL or data. |

These routes are unversioned and nonmutating. They produce HTTP lifecycle logs but not
payment-attempt metrics. Ingress restricts monitoring access; Compose binds loopback.
Each app owns a separate registry/provider and each worker is scraped separately.
POST /bulk-payments remains404; the only monetary endpoint is POST /v1/bulk-payments.

Explicit maintenance: python -m app.db.idempotency_maintenance --batch-size N --max-batches M
--timeout-seconds T. Positive finite inputs required; bounded reconciliation/deletion uses
execution guards and never executes payments. Safe aggregate diagnostics only, nonzero on
failure. Deployment scheduling and connection-budget allocation are explicit; no infinite
worker/retry loop is implied.

## Configuration and migration interfaces

Settings derives from pydantic_settings.BaseSettings. Preserve DATABASE_URL and all existing
PAYMENTS_* aliases, configured/local profiles and explicit budget validation. Normal source
precedence is explicit configuration > process environment > literal project-root dotenv >
permitted defaults. Explicit mapping/local factories disable ambient sources. No import-time
loading or os.environ mutation; no settings/secret dump in errors. New PAYMENTS_LOG_LEVEL
accepts DEBUG/INFO/WARNING/ERROR/CRITICAL case-insensitively, default INFO. Higher thresholds
intentionally suppress INFO visibility. One bounded app-owned pool serves successive lifecycle and business transactions;
physical backend reuse is allowed and no partition setting is required.

python -m app.db.migrate and alembic upgrade head use the shared safe configuration source
policy, finite waits and advisory-serialized transactional Alembic environment. Package0002
with the production image; connectivity and exact supported Alembic head required at startup. Drain
old app writers before upgrade. Preserve financial and replay state; refuse destructive
state/receipt downgrade. Missing/incompatible head never triggers automatic request DDL.

Controller, checker, payment service and database ownership/signatures are in code-design.md.
The existing async absolute deadline covers A/B/A and cleanup. COMMIT uncertainty remains503,
external cancellation propagates after bounded owned-task cleanup, and the pool's synchronous
ownership handoff remains authoritative. No hidden fresh deadline per phase.

Telemetry derives templates and supported method labels from the configured API composition, including prefixes. Unknown paths use unmatched; unsupported methods use OTHER. Probe policy belongs to route declarations. This does not change public endpoints or response bodies.

Durable success-envelope counts, totals, payment IDs and cents require JSON integers; corrupt boolean, string or floating-point representations fail closed with safe503 on replay. Valid UUID strings remain supported. Incoming amount strings and the public success shape are unchanged.
