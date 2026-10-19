# Local operations

Start: `docker compose up --build`. PostgreSQL readiness precedes the one-shot migration;
the service depends on successful migration. The image runs as UID/GID 10001 with Python
3.15 and locked runtime dependencies. Development credentials and ports are local-only.

Inspect startup with `docker compose ps` and `docker compose logs migrate service`.
`docker compose run --rm migrate` repeats schema bootstrap without replacing existing firms,
balances or payments. Incompatible imported schemas need an explicit upgrade migration.
A startup failure must be fixed before sending requests.

Stop with `docker compose down`; the normal local database volume remains. Do not remove
volumes to recover from an application error. Data initialization belongs to the operator.
`examples/firms.sql` inserts the supplied sample UUIDs without overwriting existing balances.

The SC-26 test builds from a clean source context and assigns a unique Compose project,
image tag, loopback ports and volume. Its durable ownership journal is under the current
check run's `containers/` directory. After an interrupted check, the coordinator uses
`python scripts/harness_checks.py cleanup EXACT_CHECK_RUN_DIRECTORY`. Cleanup targets only
that run's project and labelled image; it never prunes unrelated Docker resources.

## Signals and tracing

Read `/metrics` in Prometheus format; for example, scrape `localhost:8000/metrics` with
an interval chosen by the deployment. Counters distinguish committed, replayed, rejected,
processing, conflicting and failed attempts. `firm_payments_created_total` counts acknowledged new
payments only, never replays. These process-local counters are best effort, not a ledger.
Histograms use seconds with fixed instrumentation buckets, not promised latency targets.
Pool gauges read process state without financial SQL.

JSON arrival and completion logs include effective `correlation_id`, server-generated
`request_id`, safe trace/span IDs, route
pattern, bounded outcome/error categories, status and duration. They omit financial
payloads, keys, fingerprints, database URLs, raw SQL and driver messages. Uvicorn access
logs are disabled in the image so raw URLs cannot bypass redaction. Every application HTTP request, including probes, has lifecycle logs; probes remain
excluded from payment-attempt metrics. Readiness transitions are also recorded.

Telemetry variables: `PAYMENTS_LOG_QUEUE_CAPACITY` (256),
`PAYMENTS_TRACE_QUEUE_CAPACITY` (512), `PAYMENTS_TRACE_SAMPLE_RATIO` (1),
`PAYMENTS_OTLP_TIMEOUT_MS` (200), `PAYMENTS_TELEMETRY_SHUTDOWN_MS` (200),
`PAYMENTS_HEALTH_PROBE_BUDGET_MS` (1000). These are instrumentation parameters, not SLOs.
`PAYMENTS_OTLP_ENDPOINT` opts into an OTLP HTTP traces receiver, such as a locally managed
collector `/v1/traces` endpoint. Without it, logs/metrics and trace correlation remain local.
Export uses a bounded background queue; broken/full sinks can lose signals and
increment the bounded-label drop metric. Trace attributes never include SQL/financial data.
Each app owns its provider/registry. Shutdown attempts are bounded and cannot alter money.

## Failure runbook

For 503, back off and retry the identical payload with the same payer/key. Unknown commit
is not proof of rollback; inspect the durable request/payments records using authorized
operator tooling before attempting any distinct payment intent. An earlier durable 422
remains a decline even after a top-up; a genuinely new attempt needs a new key.

Investigate signals for rising 503/unknown-commit categories, readiness failure, pool
waiters/saturation and telemetry drops. Check declared C/P budgets against actual process
count, including rolling deployment overlap. Choose alert windows/thresholds from workload
and SLOs; this project supplies none. Check database health and resource pressure before
increasing limits. Restore broken telemetry sinks without retrying committed financial work.
Protect database backups, reconciliation, retention and authorization before production
exposure; those operating qualifications are outside this take-home.

## Async operation budgets

Each process owns one bounded AsyncConnectionPool on its lifespan event loop. Lifecycle and business phases use successive independent transactions/checkouts, with physical backend reuse allowed. Native connection and waiter limits apply to the single pool. Payments and readiness await database I/O. One absolute asyncio deadline stops financial work before the operation budget's configured margin, reserving it for connection/task cleanup and response construction. Expiry closes/discards the borrowed session before cancelling and joining the supervised operation. No payment task is detached. PostgreSQL timeouts remain enabled as independent backstops. A timeout during COMMIT is uncertain; retry the same key. Event-loop scheduling and host/network failures do not establish an end-to-end SLA.

Configuration loads repository-root `.env` defaults without changing `os.environ`,
then applies process environment overrides. No dotenv load happens at import time.
Explicit configuration/test mappings bypass ambient file/environment values. Both
service startup and schema bootstrap use `app.core.config.load_environment`. Keep
local values in ignored `.env`; use tracked `.env.example` as a template. Container
configuration is injected at runtime; dotenv files are excluded from the image.
Payment URL: `/v1/bulk-payments`; management endpoints remain unversioned.

Alembic owns schema history. `alembic upgrade head` and `python -m app.db.migrate`
use the same runtime configuration and transaction policy. Connection establishment,
lock waits and statements are bounded; an advisory transaction lock precedes version
inspection. Migration errors are sanitized. Do not stamp an unknown database or run
a destructive initial downgrade; retain data and investigate incompatibility.

Legacy adoption verifies the supported legacy schema through a frozen migration-only
validator. Startup checks connectivity and the supported Alembic revision only. Columns, identities, validated constraints, UUID
index/collation and timestamp defaults must match. A failed head-recording transaction
preserves legacy rows and leaves no new version state. Investigate mismatches rather
than changing or stamping a financial database automatically.

An initialization error for unsupported revision means the service has not entered its
serving lifespan. Check the deployed revision resources and explicit upgrade outcome;
do not bypass the gate with a blind stamp. Readiness only probes the already-initialized
service and database availability; it does not execute migrations or create records.

The request lifecycle revision requires stopping/draining old app writers before upgrade;
mixed old/new writers are unsupported. It preserves financial rows, identity sequences
and cached results, adds execution guards and business-result receipts, and gives existing
completed keys a full 24-hour retention window from one migration cutover timestamp.
Malformed/incomplete retained outcomes fail migration rather than become new requests.
Lifecycle downgrade is deliberately refused because it would discard safety metadata.
The migration-first intermediate image is private: its new payment writer is delivered in
the subsequent middleware/controller slice. Migration/startup verification alone does not
establish payment API delivery or acceptance.


Runtime configuration is a frozen Pydantic BaseSettings object, validated once and injected
into pool and telemetry construction. Existing DATABASE_URL/PAYMENTS_* aliases and budget
requirements remain. Normal precedence is explicit construction > process environment >
literal local dotenv > permitted defaults. Explicit local/test mapping factories bypass
ambient sources entirely, including telemetry settings; they never mutate os.environ.

One pool uses PAYMENTS_POOL_MAX and its native waiter bound; no partition setting or
aggregate statistics adapter is needed. PAYMENTS_LOG_LEVEL defaultsINFO and accepts DEBUG,
INFO, WARNING, ERROR, CRITICAL case-insensitively. Higher thresholds intentionally suppress
INFO traffic logs. Invalid configuration diagnostics omit raw URL/secret-bearing inputs.

Manage all schema changes through Alembic. Startup does not certify manually altered
objects whose Alembic revision is unchanged; investigate drift and use an explicit repair
migration or restore a known-good database rather than runtime schema repair.

The dedicated `firm_payments` logger is owned by each application instance and does not
replace root or third-party handlers. INFO records arrival and status; WARNING/ERROR
classifies failures once. DEBUG records preparation, admission, controller, guard, planning,
locks, writes, receipt, commit, publication and cleanup stages. Insert progress contains
only ordinals. Producer snapshots preserve correlation through async cancellation/cleanup;
background lifecycle events do not inherit prior request context. Malformed correlation
headers are replaced rather than logged. Sinks remain bounded and can drop records.
