# Firm Payments — proposed aligned code design

Target only; no runtime delivery is claimed. Application SQL remains async psycopg with
Pydantic schemas and plain records. SQLAlchemy Core is confined to Alembic migrations.

## Modules and direction

| Owner | Responsibility |
| --- | --- |
| app/main.py | Lifespan constructs one settings object, one bounded pool and app-owned telemetry/logger. Check connectivity and supported revision before serving; close on startup failure. |
| app/core/config.py | Immutable Settings(BaseSettings), source selection, aliases and budget validation; no import-time loading. |
| app/core/request_context.py | Scoped ContextVars for immutable correlation/request fields and safe snapshots; no HTTP/storage dependency. |
| app/core/logging.py | Dedicated structured logger, severity filter, finite event/field schema and safe exception classification; app-owned bounded sink, no root/global handler mutation. |
| app/core/observability.py | HTTP context/lifecycle middleware, metrics and trace lifecycle using injected settings and dedicated logger. |
| app/schemas/payments.py, headers.py, errors.py, idempotency.py | All request/response/header validation, canonical fingerprint, stored-result validation and safe serialization. No pool or service imports. |
| app/api/middleware/idempotency.py | Prepare payment requests once; ask checker using A; return replay/processing/conflict or admit controller; publish completed from durable receipt after B. |
| app/api/v1/payments.py | HTTP controller orchestrates admitted monetary work and receipt encoding/recording within database-owned B transaction. Maps protocol errors without SQL or transaction lifecycle. |
| app/services/idempotency.py | Independent admission, execution fencing, receipt validation, publication, failure release and one-generation recovery policy. Uses CRUD; never executes payment callbacks or owns HTTP response capture or transaction commits. |
| app/services/payments.py | Monetary rules, participant selection, funds/overflow checks, transfer planning and ordered response assembly. Calls payment CRUD for persistence. |
| app/crud/payments.py | Indexed participant locking, balance changes and payment INSERT RETURNING SQL through database.py; no business rules or HTTP schemas. |
| app/crud/idempotency.py | Claim/lookups, guard locks, receipt reads/writes and conditional publication/deletion SQL via database.py; no recovery policy or commits. |
| app/db/database.py | Pool, supervised transaction/timeout/cancellation boundaries and supported Alembic revision gate. |
| app/db/idempotency_maintenance.py | Explicit finite reconciliation/cleanup CLI and bounded per-generation work; no monetary execution. |
| app/models/idempotency.py | Typed decisions, admission token, generation/owner and opaque stored-result envelope. |
| migrations/versions/0002_request_lifecycle.py | Transactional state/guard/receipt migration and legacy retention cutover; old revision remains unchanged. |

Do not introduce a generic workflow/repository framework. Services never import each other
or middleware. The controller calls payment logic and receipt storage independently; middleware
calls the checker/completion boundary. They communicate through typed admission/result records.

## Interfaces

```python
@dataclass(frozen=True)
class Admission:
    payer_uuid: str
    key: str
    fingerprint: str
    generation: UUID
    owner: UUID

@dataclass(frozen=True)
class StoredResponse:
    status_code: Literal[201, 422]
    body: dict[str, Any]

# NewAdmission, Completed(envelope), Processing and Conflict are distinct typed decisions.
async def check(connection, identity, fingerprint) -> Decision: ...
async def acquire_execution_guard(connection, admission: Admission) -> None: ...
async def store_receipt(connection, admission: Admission, response: StoredResponse) -> None: ...
async def publish_completed(connection, admission: Admission) -> StoredResponse: ...

class BulkPaymentService:
    @staticmethod
    async def transfer(connection, request: BulkPayment) -> TransferResult: ...

# Controller-owned callback passed to the database B transaction boundary:
async def execute_admitted(connection, request: BulkPayment, admission: Admission):
    await acquire_execution_guard(connection, admission)
    result = await BulkPaymentService.transfer(connection, request)
    response = encode_and_validate_result(result, request)
    await store_receipt(connection, admission, response)
    return response
```

Signatures above show responsibility, not final driver annotation syntax. Public middleware
does not accept a user-supplied Admission object; the validated server claim populates request
state. The checker returns an opaque cached envelope; schema codec validates status/body and
request consistency before replay. Corruption or missing expected receipt fails closed.

## Persistent representation

Keep firms/payments columns, INTEGER cents and actual identity RETURNING behavior unchanged.
Keep payer/key namespace and fingerprint v1. Extend bulk_payment_requests with:

- state TEXT constrained to processing/completed; generation UUID UNIQUE; owner_token UUID.
- created_at, owner_deadline, completed_at nullable, expires_at TIMESTAMPTZ.
- existing fingerprint, nullable status_code/response_body with state consistency CHECK:
  processing has no cached result/completion timestamp; completed has 201/422, nonnull
  JSONB body and completion timestamp. Fingerprint is the existing 64-character digest.
- existing payer/key primary key and C-collated exact keys; UNIQUE(payer,key,generation)
  supports the guard foreign key. Add an expires_at index and a partial processing
  owner_deadline index for bounded maintenance selection.

request_execution_guards: generation UUID primary key; payer/key/generation foreign key
to the request row with ON DELETE CASCADE. The row is inserted atomically with each new
request claim. It is the common row lock for B, publication, recovery and deletion.

request_result_receipts: generation UUID primary key referencing its guard ON DELETE
CASCADE; owner_token, fingerprint, status_code 201/422, response_body JSONB, committed_at
TIMESTAMPTZ default database current time. Receipt is immutable after insertion. JSON body
is validated by schemas before write and on read; constraints do not pretend to validate
the complete payment response structure. No correlation headers are persisted here.

## Claim A, execute B, publish A

All database transactions are READ COMMITTED and bounded. First A INSERTs a processing
request ON CONFLICT DO NOTHING RETURNING; when inserted it also INSERTs its guard in the
same transaction. Unique conflict can wait for another short A claim, never for its B
financial transaction. A later statement reads the committed winner. Busy lifecycle locks
are classified as processing or infrastructure failure according to whether the entry can
be safely identified; do not hide database unavailability as a successful lookup.

A new claim uses database time for created_at, expires_at=created_at+24h and
owner_deadline=created_at+the declared operation budget. Entry read order is: establish
safe expiry handling; for an unexpired entry compare fingerprint; completed returns opaque
cached envelope, processing returns Processing. Never hold A while invoking the controller.

B's first lock is SELECT guard FOR UPDATE, followed by a fresh read of its request row.
Verify generation, owner, fingerprint, state=processing and owner_deadline not yet passed
at admission. A missing guard, replaced/fenced owner or stale admission aborts before firm
locks/writes. A B transaction that already owns the guard is not revoked by a clock tick;
its existing absolute operation and server transaction limits still apply.

Then lock all involved firms ORDER BY internal id FOR NO KEY UPDATE, check the complete
sum and aggregated recipient overflows, apply deltas, insert every original entry and use
RETURNING id per entry. Business rejection creates no financial effects but does create
its result receipt. Financial result and receipt commit together. Do not mutate cached
request state inside B; processing must remain visible to concurrent plain SELECTs on A.

Publication on A locks the guard, verifies owner/generation, reads the receipt and uses a
conditional UPDATE to completed, copying validated status/body. Set completion/expiry
once with database publication time +24h; an already completed identical generation returns
unchanged timestamps/result. No completion without a committed receipt. Release A before
sending the normal 201/422 response. Response capture must not cache arbitrary streaming
or transient HTTP responses; this middleware supports only the finite bulk-payment result.

## Failure, recovery and safe reuse

Processing 409 is a finite outcome, not automatic payment retry. Infrastructure failure
returns safe 503 Retry-After:1; if commit started, preserve unknown_commit. Never infer
absence of effects from a broken connection or expired timeout.

When B has definitively rolled back and its supervised task is joined, a bounded cleanup
may lock the guard, verify the same owner and absence of receipt, fence/delete that abandoned
claim and release the key. If cleanup cannot establish those facts within the deadline,
leave processing for maintenance. Do not spend an additional unbounded budget after a response.

Maintenance uses SELECT candidates ordered by timestamp/generation with finite batch/total
limits, then per-entry guard FOR UPDATE NOWAIT. Skip busy guards; no spin. A fresh request
read verifies generation. If a receipt exists, publish its exact result once. If no receipt
exists and owner_deadline passed, delete the abandoned request under the guard lock; cascade
deletes its guard. No second executor can acquire that missing generation; a new claim
gets a different generation/owner. This deletion is safe release of a failed attempt before
TTL, not expiry of a completed financial result. Invalid receipt fails closed and is alerted.

If a delayed old B was waiting for the deleted guard, it must observe zero rows or a failed
generation check and abort. It must never recreate a missing guard. Publication/recovery
also checks generation to prevent late A updates from overwriting a new request.
Maintenance never executes payment logic. Retry requests may invoke one bounded receipt-
publication attempt if they can obtain the guard; otherwise return processing 409. No
recovery path may take ownership of an active locked B generation.

For completed expiry: lock guard NOWAIT, reread and require completed with expires_at <=
database current time. Delete the old request and cascade receipt/guard in the same short
A transaction, then claim a new generation or let a later request claim. Compare-and-set
generation protects races. A completed receipt can be deleted only here, after the old
generation is fenced from execution; it cannot be removed by an unrelated age-only DELETE.
For expired processing, reconcile receipt/ownership first; a busy or unresolved generation
returns processing even past 24h. Key reuse after safely completed TTL can pay again.

Lock order is guard → request lifecycle update → firms in ascending id for B; B reads request
without a row lock and does not write it. Recovery/publication/expiry lock guard before
request UPDATE/DELETE and never acquire firm locks. Initial claim has no existing guard to
lock and never touches firms. No helper acquires these locks in reverse order. Startup and
maintenance do not run concurrently inside an application financial transaction.

CLI: `python -m app.db.idempotency_maintenance --batch-size N --max-batches M
--timeout-seconds T`, all finite positive values required; exit nonzero on command failure,
emit safe aggregate counts of completed/released/deleted/skipped/unresolved. Invalid entries
are retained with an operational failure signal. Deployment scheduling and connection
allocation must include this process; local Compose need not add a permanent worker.

## Schema lifecycle and migration

0002 transforms only a fully validated supported 0001 schema. Existing completed records
become completed, each with a generated generation/owner, guard and corresponding immutable
receipt preserving original status/body/fingerprint. Use one transactional cutover timestamp
for completed_at, owner_deadline and a full 24h expires_at for all migrated rows; retain
original created_at separately. Pre-existing incomplete committed rows cause a safe failure,
not a fabricated completion. Python UUID generation inside migration avoids an extension.

Validate existing result envelopes before adopting them. Preserve all firms/payments,
balances, sequences and stored JSON semantics. Record 0002 only with the full successful
transaction; retain advisory migration serialization and finite connection/SQL bounds.
Startup checks connectivity and the packaged 0002 head. Migrations own current schema integrity. Empty install applies
0001 then 0002; legacy unversioned adoption still verifies 0001 before advancing. Reject
destructive 0002 downgrade before mutation. Stop/drain old writers before upgrade.

## Settings

Use pydantic_settings.BaseSettings and SettingsConfigDict; lock a Python-3.15-compatible
pydantic-settings version without unrelated dependency upgrades. Base settings are frozen.
Preserve Settings.from_env(mapping=None), Settings.local(url, **overrides) and the local
URL/create_app compatibility entry points. Explicit mapping uses a source set containing
only that mapping and permitted defaults; model_validate alone must not accidentally trigger
ambient BaseSettings construction. Explicit local construction is similarly isolated.

Use validation_alias for DATABASE_URL and existing PAYMENTS_* names. Production load order
is explicit constructor > environment > project-root literal dotenv > allowed defaults.
Custom dotenv source retains dotenv_values(interpolate=False) and does not mutate os.environ.
Do not enable CLI/secrets-directory sources implicitly. Missing dotenv is valid. Ignore
unrelated environment/dotenv variables without accepting unknown explicit application fields.
Set safe validation diagnostics to exclude raw inputs; never stringify full ValidationError,
dump settings or emit connection URLs. Migration-only URL loading must not require unrelated
HTTP budget fields, while still using the same source/secret rules.

Default deployment profile is configured; every existing explicit database budget remains
required there. local supplies existing illustrative defaults. Integer fields reject bool,
fractional forms and invalid nonintegers while parsing legitimate environment integer strings.
Preserve pool_min<=pool_max, P*pool_max<=C, lock<statement<transaction, idle<transaction and
the existing budget validation. Multi-phase A/B execution additionally uses one absolute
asyncio timeout across phases, never a fresh budget per checkout or transaction.

One native AsyncConnectionPool owns the declared POOL_MIN, POOL_MAX and POOL_MAX_WAITING.
POOL_MAX may be1. No partition setting, shared admission counter, peer list or aggregate
statistics adapter is needed. A, B and publication use successive independent transactions;
physical backend reuse is allowed. Metrics read the native pool statistics. Keep the
version-bound cancelled-waiter pruning and cancellation-safe return adapters.

Existing numeric defaults remain local illustrations: C=8, P=2, pool_min=1, pool_max=4,
max_waiting=4, acquire=1000ms, connect=2s, statement=3000ms, lock=2000ms, idle=5000ms,
transaction=10000ms, operation=15000ms, response margin=1000ms, startup=10000ms.
Preserve PostgreSQL statement/lock/idle/transaction connection settings and owned task handoff.

Move all telemetry configuration out of os.environ reads in Telemetry. Retain existing
positive bounded queues/timeouts and sample ratio [0,1]. Existing aliases: PAYMENTS_
TELEMETRY_SHUTDOWN_MS, HEALTH_PROBE_BUDGET_MS, OTLP_TIMEOUT_MS, LOG_QUEUE_CAPACITY,
TRACE_SAMPLE_RATIO, OTLP_ENDPOINT, TRACE_QUEUE_CAPACITY. New PAYMENTS_LOG_LEVEL defaults
INFO and normalizes DEBUG/INFO/WARNING/ERROR/CRITICAL case-insensitively. TTL is fixed 24h
for this contract, not a new environment override. No unrequested logger framework.

## Structured event contract

Dedicated firm_payments logger emits JSON with UTC timestamp, severity, event, service,
version and request snapshot: correlation_id, request_id, trace_id/span_id when present,
normalized route and finite method. Completion adds status_code (null if not delivered),
duration and finite outcome; errors add safe category/class/validated SQLSTATE only.

INFO arrival/completion applies to every HTTP request. Payment metrics still exclude probes.
WARNING/ERROR errors are emitted once where classified; ordinary business/validation 422
is INFO. Preserve late cleanup failures as separate distinct events. DEBUG stage start/end/
failure events cover request preparation, claim/lookup, admission, guard, firms lookup/lock,
plan, balance update, each insert ordinal, receipt, commit, publication and cleanup/recovery.
Per-payment events are bounded by the 1000-entry batch. All events apply severity filtering
before formatting and use finite event/field names. Health operations use their own phases.

No bodies/descriptions/amounts/balances/firm or payment IDs/keys/fingerprints/auth headers/
query strings/SQL parameters/URLs/raw validation inputs/raw driver text at any level.
Correlation has bounded safe grammar but is untrusted; never use it as identity or metric
label. Capture context on the producing task, not in the logging worker. The bounded queue
and exporter fail independently, report drops without recursive logging and have finite
shutdown. No traceback capture with sensitive locals or library handler bypass.

## Verification

Keep real PostgreSQL Testcontainers, Linux HTTP verification and generated-ID gap oracle.
Bind SC-1–59, all outline rows and changed processing semantics. Observe real concurrent
coordination through independent pools; hold B after A claim to assert fast processing 409.
Inject failures before/after A claim, B receipt/commit and A publication; resume stale owners
after recovery/deletion; race TTL cleanup with B/duplicates. No missing Docker skip or fake PASS.
Check old 0001 completed replay preservation and transactional 0002 failure/retry.

Verify settings precedence/isolated construction/invalid startup, header matrix on all
response statuses, correlation across async cleanup, INFO/DEBUG filtering and redaction at
all levels, queue failures, metric outcomes and readiness isolation. Preserve pool handoff,
real transport cancellation/commit uncertainty and exact money/sample invariants. Fresh full
candidate checks, independent complete-source review and separate audited acceptance remain.

The runtime database module owns revision checking, not a structural schema oracle.
Immutable revisions retain their compatibility entry point, delegating only legacy
adoption verification to migrations/legacy_schema.py. Alembic DDL and PostgreSQL enforce
current constraints; migration tests verify them. No current-schema postflight oracle runs.

## CRUD/database API alignment

HTTP controllers and middleware orchestrate independent services. Services own monetary or
idempotency lifecycle decisions and call their matching app/crud modules. CRUD owns explicit
parameterized SQL; app/db/database.py owns query execution, cursor cleanup, shared connection
types, pool ownership, transactions/savepoints and operation budgets. Readiness uses the same
database API. Alembic SQL remains migration-owned. There is one bounded pool and no ORM.

Idempotency resolution acquires one guard and freshly checks the generation/owner once,
validates the durable receipt, then conditionally publishes or deletes under that lock. NOWAIT
failures roll back only a database-owned savepoint. Busy/active owners remain processing;
recovery never executes payments. Controllers call fencing, payments and receipt recording
independently in business transaction B, between admission A and publication A.

## Decoder failure boundary

Decode raw JSON separately from model validation. Decoder failures return safe422 before
admission; model validation retains its schema errors. Neither path exposes raw input.
Oversized integer conversion is covered alongside the existing safe nesting rejection.
