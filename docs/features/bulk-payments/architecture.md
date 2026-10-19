# Firm Payments — architecture

## Purpose and boundaries

Implement [FR-1–14, NFR-1–8 and INV-1–7](requirements.md) with the acceptance journeys
in [SC-1–38](scenarios.feature). This aligned target architecture is submitted for exact amendment review;
it does not declare existing code or historical tests to be an accepted delivery.

Use asynchronous FastAPI payment/readiness handlers and Psycopg AsyncConnectionPool, backed by a single authoritative PostgreSQL database. Await PostgreSQL I/O; keep each process pool on its owning event loop.
API controllers own transport/protocol mapping; Pydantic schemas own input and response
validation. A database-owned transaction composes a separate idempotency service with
BulkPaymentService, which owns only monetary transfer rules and financial SQL. Psycopg executes parameterized SQL and manages a
bounded connection pool per process. PostgreSQL owns balances, payment rows, idempotency
records, uniqueness, referential constraints and cross-instance coordination.

```text
Caller -> load balancer -> any FastAPI instance -> its bounded psycopg pool
                                                    |
                                      authoritative PostgreSQL primary
                                      firms / payments / bulk_payment_requests
```

There are no process-local monetary locks, distributed cache, queue or external payment
processor in the correctness path. A 201 response means committed database effects, not
submission to background work. Do not send writes or replay lookups to a lagging replica.

## Responsibilities and ownership

| Boundary | Owns | Must not do |
| --- | --- | --- |
| HTTP/validation | Parse UUIDs, dollar strings and keys; enforce accepted request limits; translate outcomes to HTTP. | Convert money through floating point, authorize a payer implicitly in a real deployment, or report success before commit. |
| BulkPaymentService | Ordered participant locks, funds/overflow checks, balance deltas and payment insertion. | Own HTTP, idempotency, transaction lifecycle or operation timeouts. |
| Idempotency service | Reservation, fingerprint comparison, replay validation and durable result persistence. | Commit independently of monetary effects or re-execute incomplete committed records. |
| Database operation | One transaction around idempotency and transfer, commit uncertainty and bounded cancellation cleanup. | Split one batch across transactions or commit after swallowing a failure. |
| Pool/configuration | Per-process connection lifecycle, bounded acquisition/queueing, database deadlines. | Treat a pool or event-loop task limit as a deployment-wide connection budget. |
| Database | Durable authoritative state, constraints, transactional locks and commit. | Depend on application-instance identity for uniqueness or balances. |
| Deployment boundary | Payer authorization, ingress limits/deadlines, secrets/TLS, database capacity and operations. | Assume the take-home's local deployment is real-money qualification. |

## Data model

Retain the exercise's `firms` and `payments` tables with INTEGER cents and internal IDs.
Add one `bulk_payment_requests` table for the accepted payer-scoped idempotency contract.

- `firms`: primary-key internal ID, name, canonical UUID text, nonnegative INTEGER balance.
  UUIDs are unique and indexed. Propose a unique B-tree index: PostgreSQL implements
  ordinary unique indexes with B-trees ([unique-index documentation](https://www.postgresql.org/docs/17/indexes-unique.html)).
  Firm ID and UUID are stable identities during payment operations.
- `payments`: primary-key ID; payer/payee foreign keys; positive INTEGER cents; exact
  description; payer and payee differ. Repeated recipients still create distinct rows.
  Sequence gaps after rollback are allowed; payment IDs are identities, not gapless counters.
- `bulk_payment_requests`: composite primary key `(payer_firm_uuid, idempotency_key)`,
  fingerprint, completed status/body and creation timestamp. A reservation and its completed
  response live in the same transaction. No automatic expiration or independent cleanup.
  Successful results contain the complete created-payment list with generated IDs in
  request order, so replay preserves the original IDs without reconstructing transfers.
  Response columns may be empty during that uncommitted transaction, but no successful
  application path may commit an incomplete result.

The request table deliberately has no firm foreign key: it can persist unknown-firm
rejections, and key reservation must not lock a firm before the globally ordered firm
lock set. It is an operation-result record rather than a source of firm balances.
Payment foreign keys still enforce transfer references. Use bound SQL parameters for
all request content, including description text (FR-2/4/7/9; INV-3/6).

Canonical fingerprints normalize UUIDs and exact cents and preserve payment order and
description contents. Use deterministic serialization and a versioned cryptographic digest.
The program-design stage defines the exact format and compatibility contract; changing
fingerprint semantics cannot silently reinterpret retained keys (FR-5/6).

## Transaction protocol

Propose explicit READ COMMITTED isolation with pessimistic firm-row locks:

1. Validate the request before borrowing a database connection. Derive its canonical
   fingerprint; begin one database transaction.
2. Reserve the payer/key with `INSERT ... ON CONFLICT DO NOTHING`. If it already exists,
   compare the completed record's fingerprint and replay its status/body or return 409.
3. For a new reservation, resolve the distinct payer/payee UUID set via its index and
   acquire `FOR NO KEY UPDATE` locks in ascending immutable internal-ID order.
4. Reject unknown firms, insufficient total funds or recipient overflow before any monetary
   mutation. Store the business 422 outcome in the same transaction and commit it.
5. For an affordable batch, compute exact net balance deltas, update every participating
   balance, insert each payment and complete the idempotency result.
6. Commit before returning 201. On database failure, roll back and return the retryable
   outcome required by FR-8. Always return or discard the borrowed connection safely.

A competing reservation either waits within its deadline, observes a committed result,
or can reserve the key after rollback. The subsequent read is a separate statement so
that READ COMMITTED can see a result committed after the initial insert snapshot
([PostgreSQL isolation semantics](https://www.postgresql.org/docs/17/transaction-iso.html)).
Never use one insert-snapshot query to assume the competing row is already visible.

All firm locks are acquired before balance checks and updates. `NO KEY UPDATE` excludes
competing balance writers while allowing compatible key-share checks; deterministic
ordering prevents cycles among cooperating transfer transactions. Other writers or DDL
can still cause deadlocks/timeouts: abort safely rather than promise that every operation
always succeeds ([PostgreSQL locking semantics](https://www.postgresql.org/docs/17/explicit-locking.html)).

FR-3/4/8 and INV-1/2/4/5 follow from the single commit boundary, locked current balances
and unique reservation. The invariant depends on every other balance writer respecting
these constraints and lock-order rules; direct privileged SQL can violate application
assumptions and is not an alternative supported write API.

## Failure and recovery semantics

| Failure point | Durable outcome | Caller action |
| --- | --- | --- |
| Malformed input or invalid key | No reservation, payments or balance effects. | Correct input; 422. |
| Business rejection after reservation | Stable 422 result; no monetary effects. | Retry replays it; a new intended attempt uses a new key. |
| Mismatched reuse | Original result/effects unchanged; 409. | Use the original payload or a new key for a new operation. |
| SQL failure or lock/deadline expiry before commit | Entire transaction/reservation rolls back. | 503; retry the same key with backoff. |
| Commit or response acknowledgement lost | Commit status is uncertain to the caller. | Retry the same key; replay the commit or execute once after rollback. |
| Unexpected committed incomplete key record | Invariant breach; never execute it again blindly. | Fail closed, alert and reconcile with database evidence. |

There is no automatic application transaction-retry loop. Retrying outside the service
uses the same key. Durable idempotency provides at-most-once committed monetary effects,
not guaranteed delivery of an HTTP response. Idempotency data must share backup/restore
consistency with payment and balance data.

## Resource policy and missing deployment inputs

No measured workload, instance-count ceiling, database connection allocation or request
deadline has been supplied. Therefore the previous candidate's pool/timeouts and test
counts are not evidence for a production configuration.

Propose deployment-configured finite limits with startup validation:

- Let `C` be the database connection allocation for this service after administrative and
  other-client reserves, and `P` the maximum number of service processes, including surge
  replicas. Require `P * pool_max <= C`; minimum pool size must not exceed its maximum.
- Bound the pool waiting queue separately from active connections. Reject overload within
  a finite acquisition deadline; do not use an unlimited waiting room.
- Let `D` be the caller/deployment operation deadline. Allocate finite acquisition, checkout
  validation, transaction and response margins such that their sum fits `D`. Bound connection
  establishment, SQL statements, lock waits and idle transactions as well. A statement
  timeout alone does not bound the whole multi-statement operation.
- Select a finite transaction/operation deadline and release/rollback resources on expiry.
  Program design must specify the PostgreSQL/driver mechanism and error translation.
- Supply local development values as an explicitly illustrative profile, not a capacity
  qualification. Production values require the real `C`, `P` and `D` and workload evidence.

The program-design package will propose the configuration interface and a labeled local
profile. Undeclared production budgets must not be silently replaced with the existing
implementation constants. Psycopg exposes bounded pools and acquisition waits, but its
per-process limits still need deployment-level allocation
([pool documentation](https://www.psycopg.org/psycopg3/docs/advanced/pool.html)).

## Deployment, integration and schema lifecycle

Use one non-root Python3.15 service image with PostgreSQL17. Package alembic.ini and
migrations/ with the application and locked production dependencies. Compose waits for
PostgreSQL readiness, runs an explicit one-shot Alembic upgrade, then starts app.main:app.
Application handlers/startup never perform DDL or silently upgrade an existing database.

Alembic revisions own schema history. SQLAlchemy Core provides only the migration
connection; application operations remain async psycopg SQL, without an ORM. The initial
revision creates the existing three application tables, constraints and UUID index; Alembic
adds its version table. app/db/migrate.py is an upgrade-head wrapper, not a second DDL path.

Acquire a PostgreSQL advisory transaction lock before Alembic inspects/creates version
state. Bound connection establishment, lock and statement waits. One outer transaction
includes schema changes and version recording; failure rolls both back. Repeated/concurrent
calls converge to the supported head or fail within configured finite waits (FR-12/NFR-8).

For an unversioned database, distinguish an empty application schema from a legacy schema.
Create the former. Verify the entire latter against the exact supported structure before
adoption; preserve financial rows, retained responses and sequence identities. Reject
partial/incompatible schemas without stamping or destructive repair. Reject the initial
destructive downgrade. Future migrations require their own compatibility decisions.

Startup validates structure and supported head revision before serving requests. Missing,
older or unknown version state fails initialization. Readiness uses the initialized schema
contract and a bounded read-only availability probe; management calls do not run migrations.
Recovery uses transactional rollback, safe retry or an operator-owned backup restoration;
no API or deployment step resets financial data automatically (FR-10–14/INV-7).

## Observability boundary

Operational telemetry is a separate, best-effort side channel: JSON logs to stdout,
process-local Prometheus metrics and OpenTelemetry traces with optional bounded OTLP
export. Use request and trace correlation to diagnose attempts without logging financial
payloads, customer identities or raw idempotency keys. Logs/metrics are not a durable
settlement ledger and cannot prove a transfer did or did not commit after a crash.

Observe outcomes, latency, pool acquisition/pressure and database failures, with distinct
committed versus replayed results. Count committed effects only after acknowledged commit;
uncertain acknowledgement is its own outcome. Exporters never run synchronously while
holding financial locks. Bounded telemetry queues may drop data under overload; expose
that degradation and preserve database correctness. Raw driver exception text and automatic
SQL/parameter capture are disabled to prevent data leakage.

Liveness is independent of PostgreSQL. Readiness checks initialized schema/pool state and
a bounded read-only probe. Metrics are available without querying the financial database.
Operational routes require trusted network exposure. One service worker per container
keeps per-process metric scraping explicit; aggregate instances in the monitoring system.
The take-home supplies instrumentation and a short operations guide; operating a hosted
collector, dashboard/alert service and retention infrastructure remains deployment work.

## Verification strategy

Verification parameters derive from the failure model rather than candidate test constants:

- Exercise each invariant and SC-1–38 against real PostgreSQL, including the exact SC-21
  seed/request and persisted three-payment outcome.
- Force overlapping operations across independent service instances; demonstrate that
  overlap with database wait/transaction evidence. Select request counts sufficient to
  expose stale-funds checks, duplicate reservation races and opposing lock acquisition.
- Inject failures after monetary mutation begins and around response delivery; prove
  rollback or safe replay, not merely a matching status code.
- Choose UUID population/selectivity from documented workload assumptions and inspect
  query plans. No fixed data-set size establishes a general performance guarantee.
- Check resource settings and exhaustion/deadline behavior against the declared profile.
- Capture structured logs and in-memory trace export to verify safe fields/correlation;
  check metric deltas and label allowlists for commit, replay, rejection and failure.
  Simulate telemetry outage/full queues and database outage; assert independent health
  semantics, no leaked financial fields and no changed transfer/idempotency outcomes.
- Verify reproducibility from fresh inputs and retain candidate-bound environment/log/resource
  evidence. Execute through the audited project-local trusted-host policy; do not claim native
  worker isolation. Verify Linux HTTP and PostgreSQL persistence, not only in-process APIs.

Exact executable checks, coverage IDs and reproducible environment prerequisites belong in
the program/slice packages. Historical PASS records establish only their bound candidate;
source changes require current integration evidence.

## Alternative and tradeoffs

The material alternative is SERIALIZABLE isolation with whole-transaction retry on
serialization failure. It can protect broader future predicates, but adds retry/deadline
handling and requires idempotency to remain correct across aborted attempts. For this
bounded transfer over a known firm set, explicit ordered locks make the funds decision
and contention behavior easier to audit. Reconsider if requirements later depend on
cross-row predicates beyond the locked set.

The selected approach deliberately serializes operations involving a hot firm; a larger
pool cannot remove that bottleneck. Locked-batch work, growth of retained idempotency
records, external balance writers and deployment deadlines are the principal operational
risks. They need measurement and deployment policy, not invented product throughput claims.

## Package and configuration ownership

app/main.py owns lifespan and resource construction. app/api/router.py composes the payment
router under /v1 and unversioned operations routes. app/api/v1/payments.py extracts headers
and maps domain/infrastructure outcomes; app/schemas owns Pydantic validation/sanitization.
app/models contains plain records. app/services separates payments and idempotency; app/db
owns transaction, pool and compatibility boundaries. Only the migration environment uses
SQLAlchemy Core; no application ORM or generic repository abstraction is introduced.

Configuration reads repository-root .env defaults with python-dotenv, then overlays process
environment. Explicit mappings bypass ambient loading; values are literal, and loading does
not mutate os.environ or occur at import time. Startup and migration share this loader.
Track .env.example and exclude actual dotenv files from Git/images. Local defaults are not
production capacity claims; configured deployment budgets remain mandatory for that profile.

## Async request and pool ownership

One supervised payment task uses a single asyncio.timeout_at deadline covering acquisition,
checkout validation and all transaction work. The response margin reserves cleanup. Keep
server timeouts as independent backstops and avoid per-phase deadline resets.

Before handoff, the request owns the borrowed session: on expiry/cancellation, close it
before cancelling and joining financial work. Return transfers ownership synchronously to
pool maintenance, with no cancellable gap. After that transfer the request must never close
a session another caller may borrow. The pinned pool adapter owns disposal/replacement and
capacity accounting; ordinary nonfinancial maintenance may complete asynchronously. Join all
financial operation/cleanup tasks within the budget and preserve caller cancellation. Pool
waiter pruning and handoff adapters are version-bound risks requiring actual pre/post-handoff
race checks. COMMIT uncertainty always resolves through the same key, never a new execution
based solely on a lost acknowledgement (NFR-3/FR-8/INV-4).

## Contract status

This describes the aligned target, not delivered Alembic behavior. Current pre-Alembic source
has authenticated local checks for SC-1–30. New SC-31–38 and the complete current source still
require implementation, candidate-bound checks, independent review and audited acceptance.
