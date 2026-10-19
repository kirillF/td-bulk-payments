# Firm Payments — code design

Implements the accepted [product](requirements.md), [architecture](architecture.md) and
the proposed [API](api.md). Module boundaries below assign responsibilities; they do not
freeze private helper bodies or require one abstraction per function.

## Modules and dependency direction

| Module | Responsibility and verification boundary |
| --- | --- |
| `app/schemas/` | Pydantic request, response, error and key schemas; all shape/domain validation, sanitization, exact cents and canonical fingerprint v1. No database/pool imports. |
| `app/models/` | Plain transfer plan/result and durable replay result records. No ORM. |
| `app/core/` | Immutable settings and structured observability. |
| `app/db/` | Async pool, transaction boundary, implicit timeout/cancellation lifecycle and schema/revision validation. |
| `app/services/payments.py` | BulkPaymentService: ordered firm locking, balance-dependent validation, deltas and payment insertion only. No HTTP, idempotency or transaction lifecycle. |
| `app/services/idempotency.py` | Separate reservation/replay/result persistence service inside the caller-owned transaction. |
| `app/api/v1/payments.py` | HTTP controller, header cardinality, status/header/error mapping; router mounted at /v1. |
| `app/api/operations.py`, `app/api/errors.py` | Unversioned health/metrics and safe protocol validation errors. |
| `app/api/router.py`, `app/main.py` | Route composition, application factory/lifespan and resource wiring. |
| `alembic.ini`, `migrations/`, `app/db/migrate.py` | Versioned migration resources, advisory-locked Alembic environment and upgrade wrapper; no ORM. |
| `tests/`, `scripts/` | Full registered checks, executable Gherkin, real PostgreSQL faults and container verification. |

Keep source organization small. No ORM, generic repository hierarchy, Redis, queue or
distributed-lock abstraction. Source edits must stay within the formally approved slice
ownership; shared manifests, checks, fixtures and environment adapters need explicit owners.

## Types, lifetimes and transaction ownership

- One immutable Settings instance and one pool per service process. Lifespan opens/waits
  within a startup deadline and always closes the pool, including startup failure paths.
- Each asynchronous route borrows one connection for one operation. A transaction context
  encloses every database effect and completes before a result can leave the database operation.
- Never share a borrowed connection between requests. No global mutable request state,
  cached balances or local mutex grants monetary authority.
- Request input remains logically immutable through fingerprint and execution. Aggregate
  repeated payees in a local integer-delta map; retain every original payment entry for
  insertion. Validate all resulting balances before mutations.
- Explicitly lock all known participating firms in ascending internal ID before checking
  money or updating rows. Stable IDs/UUIDs and cooperating external balance writers are
  assumptions of this protocol. All monetary SQL is parameterized.
- Reservation conflict is resolved in a later READ COMMITTED statement. An existing row
  must have a matching fingerprint and completed status/body before replay. An incomplete
  committed row raises a dedicated invariant-breach error; never delete/re-execute it.
- Store the completed response before commit. A disconnect during commit is ambiguous;
  HTTP maps the infrastructure failure to 503 without claiming rollback certainty.
- Obtain each inserted payment's generated ID with SQL RETURNING and retain its association
  with the corresponding request entry. Do not infer IDs from sequence arithmetic or assume
  unordered bulk RETURNING rows match request order. Build typed PaymentCreated objects
  using those persisted IDs, canonical public firm UUIDs, cents and exact descriptions.
  Store the full ordered BulkPaymentResponse in the idempotency JSON before commit;
  parse replay into the appropriate success/error response type without generating new IDs.

## Settings and deployment budget validation

Environment names are prefixed `PAYMENTS_`, except the secret connection string
`DATABASE_URL`. Never log that string. `PAYMENTS_PROFILE` is `local` or `configured`;
default is `configured`, which requires explicit values. Compose explicitly selects local.

| Setting | Meaning |
| --- | --- |
| `DB_CONNECTION_BUDGET` | Service-wide database allocation C, after other-client/admin reserves. |
| `MAX_PROCESSES` | Maximum simultaneous service processes P, including rolling-deploy surge. |
| `POOL_MIN`, `POOL_MAX`, `POOL_MAX_WAITING` | Per-process connected minimum/maximum and finite positive waiting capacity. |
| `ACQUIRE_TIMEOUT_MS` | Maximum pool acquisition wait. |
| `CONNECT_TIMEOUT_SECONDS` | Finite libpq connection establishment timeout. |
| `STATEMENT_TIMEOUT_MS`, `LOCK_TIMEOUT_MS` | PostgreSQL per-statement and per-lock bounds. |
| `IDLE_TRANSACTION_TIMEOUT_MS`, `TRANSACTION_TIMEOUT_MS` | Idle transaction/session and whole transaction bounds. |
| `OPERATION_BUDGET_MS`, `RESPONSE_MARGIN_MS` | Post-validation operation planning budget D and response/processing allowance. |
| `STARTUP_TIMEOUT_MS` | Maximum wait for initial pool readiness. |

Every listed numeric setting must be positive, except `POOL_MIN` may be zero. Reject
non-integers, `POOL_MIN > POOL_MAX`, `P * POOL_MAX > C`, lock timeout >= statement timeout,
or statement/idle timeouts >= transaction timeout. Require:

```text
ACQUIRE_TIMEOUT_MS + STATEMENT_TIMEOUT_MS + TRANSACTION_TIMEOUT_MS
    + RESPONSE_MARGIN_MS <= OPERATION_BUDGET_MS
```

The statement term reserves a bounded checkout-health query; transaction time includes
all transfer SQL. In the HTTP operation acquire once, check once, then transact; do not
allow repeated health-check attempts to consume an unbounded multiplier. Failed checkout
health checks discard the connection and fail this request with 503; a later request may
try again. Connection establishment occurs in bounded pool background workers, while
the waiting request remains subject to its acquisition limit.

Propose the following **local development profile**, not a production capacity claim:

| Parameter | Local value | Reason |
| --- | --- | --- |
| C / P | 8 connections / 2 processes | A deliberately small service allocation supporting a two-process local concurrency exercise. Other database capacity is unallocated. |
| Pool min / max / waiters | 1 / 4 / 4 | 2 × 4 fits C; permit at most one additional queued request per connection slot before overload rejection. |
| Acquisition / connect | 1000 ms / 2 seconds | Short local queueing; disconnected pool workers cannot hold callers indefinitely. |
| Statement / lock | 3000 / 2000 ms | A contended lock fails before the statement limit; these are generous local SQL budgets, not measured latency targets. |
| Idle transaction / whole transaction | 5000 / 10000 ms | Reclaim stalled transactions and bound a multi-statement batch beyond individual statement limits. |
| D / response margin | 15000 / 1000 ms | 1000 + 3000 + 10000 + 1000 fits the declared post-validation planning budget. |
| Startup readiness | 10000 ms | Bound local database/pool startup independently from request handling. |

These values are a transparent developer profile chosen to exercise bounded behavior,
not values validated by existing candidate tests. A production deployment must select
`configured`, supply its real C/P/D and validate workload suitability. The algebra is
necessary budget consistency, not proof of an end-to-end latency SLA.

## Deadlines, cancellation and cleanup

Configure PostgreSQL session `statement_timeout`, `lock_timeout`,
`idle_in_transaction_session_timeout` and `transaction_timeout` on each new connection.
PostgreSQL 17's transaction timeout terminates a session whose transaction lasts too
long; a lost connection is discarded/replaced rather than returned as healthy
([PostgreSQL timeout documentation](https://www.postgresql.org/docs/17/runtime-config-client.html)).
Install the whole-transaction timeout before starting payment transactions, so it covers
reservation waiting, SQL work and commit. Do not use prepared/two-phase transactions.

Use asynchronous FastAPI payment/readiness handlers and one app-owned AsyncConnectionPool on the lifespan event loop. Await every financial SQL/cursor/pool operation. Keep migration CLI and independent fixture/inspection SQL synchronous; they do not run in the request transaction.

Compute one absolute deadline from loop.time() and the existing operation budget. Stop financial work before the existing response margin, which reserves bounded connection/task cleanup and response construction. Use asyncio.timeout_at around the complete operation; never grant a new full timeout to each query. Server statement, row-lock, idle and transaction timeouts remain independent defenses.

A request owns one supervised financial child task awaited through asyncio.shield inside
the timeout boundary. Operation records track connection ownership and commit state. While
BORROWED, close/discard the owned session before cancellation, then join the child within
cleanup reserve. Successful checkout registers ownership without another await.

The return adapter transitions BORROWED -> POOL_OWNED synchronously by accepting the return
into pool maintenance; clear request ownership at that point. Never infer handoff from an
attempted return that can still suspend. Once POOL_OWNED, request cleanup cannot close the
session, which may already be reassigned. Maintenance handles disposal/replacement and pool
capacity; it performs no financial work. Retain the narrow, locked psycopg-pool adapter and
real pre/post-handoff cancellation/timeout regressions. Financial tasks and request cleanup
are joined; ordinary pool maintenance remains owned by the pool's bounded lifecycle.


If COMMIT has started without an acknowledged outcome, report an uncertain commit (503 for a connected caller), preserve safe outcome telemetry and require identical-key recovery. An acknowledged commit or replay never becomes a second monetary execution because a response was lost. External cancellation propagates after cleanup; it does not fabricate a response to a disconnected caller. No unbounded retries, wait loops, detached operation tasks or concurrent commands on one connection. Verify pool waiters, cancellation/cleanup, stalled transport and event-loop responsiveness against the configured bounds, without claiming a strict caller-visible SLA.

## Schema and migration details

- `firms`: identity INTEGER primary key, nonnull name/UUID/balance; canonical UUID text,
  unique UUID B-tree index; nonnegative balance check; INTEGER provides the upper bound.
- `payments`: identity INTEGER primary key, nonnull payer/payee foreign keys and description,
  positive amount check and payer != payee check. Do not require gapless identity values.
- `bulk_payment_requests`: payer/key composite primary key, 64-character SHA256 digest,
  nullable status/body during reservation with consistent nullability; completed status
  is 201 or 422. Created timestamp is for diagnostics, not expiration authority.
- Alembic owns ordered revisions and its version table; remove standalone schema.sql.
  The initial revision owns the same DDL, indexes, constraints and sequence semantics.
- migrations/env.py creates a migration-only SQLAlchemy Core connection from runtime
  configuration (NullPool), with finite connect/lock/statement bounds. Acquire the advisory
  transaction lock before invoking Alembic version inspection/creation, inside one outer
  transaction. Configure Alembic with that connection and target_metadata=None; there is no
  ORM metadata or automatic production autogenerate/stamp path.
- app/db/migrate.py delegates to Alembic upgrade head; direct CLI and wrapper share the same
  environment/transaction policy. Do not maintain a second CREATE IF NOT EXISTS bootstrap.
- Before an unversioned legacy adoption, verify every supported table/column/type/nullability,
  identity/PK/FK/check/index and key collation. Empty application state may be initialized;
  partial or incompatible schemas fail without stamping. Preserve all existing rows and
  identity sequence state. Reuse a focused schema verifier rather than duplicate a weaker
  migration-only check. Adapt synchronous migration and async startup boundaries explicitly.
- Startup validates exact supported revision as well as structure. Resolve the packaged
  single head from revision resources; missing/older/unknown revision fails initialization.
  Revision checks are read-only and do not make each health probe execute migration code.
- Reject the initial destructive downgrade before executing DDL/version updates. Atomic
  migration failure rolls back changes and head recording; safe retry is explicit.
- Explicitly seeded IDs in SC-21 must not be treated as proof that the identity sequence
  has advanced. Test fixtures own fresh state and sequence setup; production import
  sequence maintenance is separate from the bulk-payment API.

## Verification design and coverage boundaries

Pure checks cover exact money grammar/range, canonical fingerprint equivalence versus
conflict, settings validation and budget algebra. SQL/API checks use PostgreSQL 17 through
Testcontainers; they must assert durable rows and balances, not status alone.

Gherkin bindings must implement SC-1–38, including every outline row and the exact SC-21
SQL/JSON/three-payment result. Reusing old tests does not replace binding missing scenarios.
The test harness must explicitly override Background balances where a scenario supplies
its own seed; fixtures may not accidentally append duplicate firms.
For every success, compare the returned ordered payment objects and IDs with persisted
rows and request entries. Include repeated recipients, sequence gaps, same-key replay and
concurrent requests so an assumed contiguous-ID range cannot satisfy the oracle.

Concurrency verification uses a held database lock and observed server wait state to
establish overlap. Derive participant count from the hypothesis: at least competing
requests across separate process-local pools, enough distinct-key spending attempts to
exceed funds, repeated identical keys to detect duplicate execution, and opposing transfer
paths that could form a lock cycle. Counts are documented fixtures, not product capacities.

Choose index-test population/selectivity to model the stated thousands-of-firms domain;
document the chosen sample and inspect index/EXPLAIN evidence without forcing planner
settings to make a nonrepresentative plan pass. No benchmark throughput claim is inferred.

Failure checks cover insertion failure after balance mutation, key-conflict races, rollback
followed by same-key retry, lock/statement/whole-transaction deadlines, pool overload,
incomplete durable records, lost HTTP responses and expired operation budget. Verify that
server-side deadline termination leaves no partial monetary changes and the pool recovers.

Container verification builds the service, executes Alembic upgrade head, verifies non-root Python
3.15 startup, submits SC-21 over real HTTP, checks persisted results and same-key replay,
and cleans up only its own containers/network. Isolated check adapters create their own
virtual environments/dependencies and Docker prerequisites under approved harness boundaries;
project-local trusted-host verification is explicitly authorized by
PROJECT_OVERRIDES.md; it is not a claim of shared native isolation. Preserve candidate,
environment, log and resource-artifact bindings and fail when prerequisites are unavailable.

## Structured logging and correlation

Use standard-library logging with a JSON formatter and an app-scoped Telemetry interface.
Propose `prometheus-client` for metrics and OpenTelemetry API/SDK plus OTLP HTTP exporter
for tracing; lock versions through the formal slice's manifest ownership. No collector
is required to run the local payment service. Automatic request-body/SQL instrumentation
is excluded; create explicit safe spans rather than exporting statements/parameters.

Generate a new request UUID at ingress, before validation, and propagate it explicitly
through async request/operation tasks and safe logging context. Clear/reset context after every
attempt to prevent cross-request leakage. Extract valid W3C trace context using the SDK,
without arbitrary baggage; logs include trace/span IDs when present. Replays have new
attempt IDs and preserve original returned payment IDs.

Emit one `http.request.completed` summary per payment attempt when logging is healthy.
The allowlisted event schema is:

```text
timestamp (UTC RFC3339), level, event, service, version,
request_id, trace_id?, span_id?, route (template), method,
status_code?, duration_ms, outcome, error_code?, error_type?, sqlstate?
```

Use finite outcome/error-code vocabularies: committed, replayed, business_rejected,
validation_rejected, conflict, transient_failure, unknown_commit, invariant_breach,
client_disconnected. Unknown routes use one `unmatched` template, not the raw URL.
Service/version come from deployment metadata, not request input. Missing status after a
disconnect remains null; never invent a delivered response. Use INFO for normal outcomes,
WARNING for transient/uncertain failures, ERROR for invariant breaches. Startup/shutdown,
readiness state transitions and telemetry degradation have separate bounded events.

Never emit request/response bodies, descriptions, amounts/balances, firm/payment IDs,
idempotency keys or payload fingerprints, authorization/cookie headers, query strings,
database URLs, SQL text/parameters, raw validation input or raw driver exception messages.
Allow exception class and a validated SQLSTATE only. Do not automatically record raw
exception events/tracebacks containing source/locals/driver text. Apply the same policy
to framework/server access/error logging; JSON-encode user-controlled values rather than
allowing log-line injection. Debug level does not bypass redaction.

## Metrics, traces and operational semantics

| Metric | Type and allowed labels | Meaning |
| --- | --- | --- |
| `firm_payments_http_requests_total` | Counter: route template, method, status class | Payment HTTP attempts, including validation/errors; probes/scrapes excluded. |
| `firm_payments_http_request_duration_seconds` | Histogram: route template, status class | Server attempt duration using monotonic time, not network delivery latency. |
| `firm_payments_attempts_total` | Counter: outcome | Distinguish a new committed result from replay/rejection/conflict/failure. |
| `firm_payments_created_total` | Counter: no request labels | Number of payments newly committed with acknowledged commit; never increment on replay. |
| `firm_payments_pool_acquire_duration_seconds` | Histogram: bounded result enum | Pool acquisition delay and failures. |
| `firm_payments_db_transaction_duration_seconds` | Histogram: bounded result enum | Transaction duration, including rollback or unknown-commit classification. |
| `firm_payments_db_failures_total` | Counter: finite category enum | Pool exhaustion, lock/statement/transaction timeout, connection failure or invariant breach. |
| `firm_payments_pool_connections`, `firm_payments_pool_waiters` | Gauges: fixed state enum only where needed | Process-local pool pressure; reads pool state without a financial SQL query. |
| `firm_payments_telemetry_dropped_total` | Counter: bounded signal enum | Locally observed recording/export queue drops, when instrumentation can still report them. |

Never label metrics by request/trace ID, firm/payment UUID/ID, key, fingerprint, description,
raw path or exception message. Use seconds for duration metrics and fixed bucket boundaries
chosen from the declared operation/deadline profile; document them as instrumentation
parameters, not latency requirements. Metric definitions follow
[Prometheus cardinality/naming guidance](https://prometheus.io/docs/practices/naming/).

Trace the HTTP attempt and bounded child phases `pool.acquire`, `payment.transaction`,
`idempotency.resolve`, `firms.lock`, `payments.persist`, `transaction.commit`. Attach only
allowlisted route/status/outcome/error categories. A replay has no persist/commit-effect
span claiming a new transfer. Mark ambiguous commit explicitly; do not label it rolled back
solely because the acknowledgement failed. Do not export raw SQL or financial attributes.

Use an app-owned tracer provider/exporter lifecycle and separate Prometheus registry to
avoid duplicate registration or shared test state. OTLP export is opt-in with bounded
batch queue, export timeout and shutdown flush; record/export after financial locks are
released. Sampling ratio, queue capacities and timeouts are explicit configuration, not
deployment SLOs. Local profile: JSON logs + metrics, in-process trace context, no external
export. Tests use an in-memory exporter with deterministic sampling. Reference:
[OpenTelemetry Python export](https://opentelemetry.io/docs/languages/python/exporters/).

Logs use a bounded nonblocking queue to stdout; full/broken sinks may lose events and
increment the drop signal, with a rate-limited sanitized degradation event where possible.
No telemetry call retries indefinitely, propagates a sink exception into a transaction,
or changes a committed/replayed response. No guarantee of operational-event delivery
across process crashes is made. Counters may miss a commit if the process dies immediately
after it; only database records are financial truth. A ledger/outbox audit stream would be
a separate product decision, not an implied property of JSON logs.

Operational routes use the API contract. Bound readiness to a configured health-probe
budget and pool/query deadlines; it must not inherit a long payment queue indefinitely.
Liveness and metrics require no database access. Suppress normal per-probe completion
logs; emit readiness state changes instead. Readiness includes initialization/schema
compatibility and database/pool availability, not guaranteed acceptance of every batch.

Add an operations guide with metric/scrape examples, log correlation, signals for 503 and
unknown-commit spikes, readiness failure, saturation and telemetry drops, plus a same-key
retry/reconciliation runbook. Propose alerts but leave windows/thresholds to deployment
workload/SLO decisions. No invented paging targets or hosted monitoring deployment.

Verification must capture JSON logs, metric deltas and in-memory spans for new commit,
replay, rejection, conflict, validation error and SQL failure. Assert correlation survives
concurrent async handlers without leakage; inject canary secrets/payload text and verify
absence across every telemetry signal. Check replay never counts new payment effects,
each app registry is isolated, probes are non-mutating, liveness survives database outage,
readiness fails within its budget, and full queues/exporter failure leave transfers and
same-key retry behavior correct. Do not use metric counters to assert a durable ledger.

Exact argv, ownership and requirement/scenario coverage belong in the current slice/check
definitions. The project-local trusted-host exception governs Docker execution. Missing
Docker/network prerequisites fail their checks rather than silently reducing coverage.

## Description sanitization amendment

Use a Payment description field validator in before mode. For string input, remove U+0000 and isolated U+D800–U+DFFF code points before length validation; pass non-string input through unchanged so StrictStr still rejects incorrect types. Preserve every other character exactly, including whitespace and supplementary characters decoded from valid JSON surrogate pairs. Use the sanitized model for existing v1 fingerprints, PostgreSQL TEXT, durable JSONB responses and replay. Do not sanitize amounts, UUIDs, keys or descriptions already persisted. SC-27 verifies successful sanitized transfers and same-key equivalence; SC-28 verifies supported text preservation. No storage migration is required.

## Async verification ownership

Keep every app/pool on one persistent test event loop for its complete lifespan and requests. Adapt synchronous pytest-bdd/inspection fixtures through a TestClient/portal owning that loop; do not open an async pool on one loop and issue requests from fresh asyncio.run loops. Retain independent pools/instances, real PostgreSQL lock contention, generated-ID gaps and deliberate mutation oracle, null sanitization/replay, redaction, trace queue pressure, health bounds, real TCP reply withholding at checkout/SQL/COMMIT/ROLLBACK, and final container HTTP checks. SC-29 verifies a waiting database operation does not block a concurrent liveness request.

## Dotenv configuration

app/core/config.py reads the repository-root .env with python-dotenv, then overlays
process environment values. Explicit configuration mappings bypass ambient loading.
Loading is deferred to startup/bootstrap, does not mutate os.environ, and uses literal
values without variable interpolation. Missing .env is valid with supplied environment.
Existing profile/limit validation remains mandatory. Track .env.example with local-only
values; keep actual .env files out of Git and images and preserve existing local files.
Declare the already-locked python-dotenv1.2.4 as a production dependency; no upgrades.

## Migration verification derived from requirements

SC-31–38 cover empty creation, verified legacy preservation/replay, incompatible/partial
schema rejection, idempotent repeated upgrades, actual concurrent upgrade contention,
transactional failure/retry, destructive downgrade denial and unsupported-revision startup.
Use real PostgreSQL and observe actual overlapping migration execution. Compare all prior
financial/request rows and sequence state, not only row counts or process exit codes.
Inject migration failure inside a transaction after DDL, and assert neither DDL nor revision
survives; retry must then converge safely. CLI errors and logs must exclude synthetic secrets.
Run all prior financial/async/sanitization/versioned HTTP cases against the migrated schema.

The target is38 scenarios/70 expanded cases; specification is not delivery. Exact task and
check mappings are in feature.json and slices.md. Historical requests/deliveries stay in
private audit state; current documents describe one coherent target rather than appended
conflicting generations. Granular local source commits follow docs/harness/COMMIT_POLICY.md;
Git commits do not waive checks, independent review or final acceptance.
