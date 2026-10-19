# Firm Payments — proposed aligned architecture

This target extends the accepted correlation/settings/idempotency design. It is not
runtime delivery evidence. Requirements and scenarios define behavior; code-design.md
defines the concrete protocol. The app remains FastAPI/Pydantic, async psycopg SQL,
PostgreSQL 17, Python 3.15, without an application ORM.

## Responsibilities

Correlation middleware establishes request context before idempotency middleware. A
route-scoped idempotency middleware uses schema validation and a lightweight checker to
claim a request, replay a completed response or return 409 for a processing entry.
Only admitted requests reach the versioned HTTP controller. The controller orchestrates
the monetary use case, encoding its validated result and recording the business receipt.
BulkPaymentService performs only monetary operations. Neither payment service nor
idempotency checker calls the other or accepts a callback that executes the other.

Database helpers own pooling, transactions, timeouts, cancellation and cleanup. Connection
A handles short request lifecycle transactions; B handles the financial transaction.
Both use successive checkouts from one bounded app-owned pool; physical sessions may be
reused. Release A before acquiring B and release B before publication checkout.
No connection, transaction or lock is held across these phases.
No process-local cache or lock decides payment authority. Read/write coordination uses
the same PostgreSQL primary; no lagging replica or Redis authority is introduced.

## State and data

Keep firms/payments, exact integer cents, response IDs and unique canonical UUID index.
Migrate bulk_payment_requests to explicit processing/completed state, owner token,
generation UUID, fingerprint, creation/completion/expiry timestamps, and cached status/body.
Add one execution-guard row per request generation and one immutable business-result
receipt, atomically written with B's financial changes. Constraints, indexes, legacy
cutover and lock order are specified in code-design.md.

The request entry is a status/cache, not financial truth. A committed receipt proves the
business result even if cache publication never happened. The guard serializes execution,
recovery and deletion. Expiry alone never proves that an earlier owner stopped executing.
Receipts and guards cannot be deleted while any generation remains executable or unresolved.

## Protocol

1. Schema validation and normalization occur before A creates an entry. Invalid requests
   return safe 422 without claiming a key; the same prepared input reaches the controller.
2. An atomic claim on A commits processing with a generation/owner. Existing completed
   entries replay; existing processing entries return 409, not wait for B to finish.
   Changed fingerprints under an unexpired entry return key conflict.
3. The controller asks the database boundary to execute B. B locks the generation guard,
   verifies current owner, then locks involved firms in ascending internal ID. Check the
   entire batch before writes; preserve each payment entry and obtain IDs from RETURNING.
4. Encode a validated success or business rejection. Record the receipt in B's transaction.
   Only an acknowledged commit can count new financial effects. Failure rolls back both
   receipt and all financial changes; the A claim may remain processing until resolved.
5. On a separate A transaction, lock the guard and publish completed from the receipt,
   conditional on the same generation/owner. Only then send the normal response. Publication
   failure returns safe 503 for a connected caller; it cannot undo B's committed effects.
6. Recovery acquires the same guard without unbounded waiting. A receipt completes the
   cache without payment execution. No receipt plus a lapsed ownership lease permits
   fencing the old owner and releasing the claim for a new attempt. A stale owner must
   fail its guard/owner check. Busy guards remain processing.

No transaction spans A and B. Their cross-transaction consistency is supplied by the
receipt plus fencing protocol, not by independently updating a status field. No two-phase
commit, autonomous payment retries or background monetary execution is introduced.

## TTL and recovery

Processing expiry is creation plus 24 hours; completed expiry is publication/completion
plus 24 hours using database timestamps. The first publication fixes completion time;
replay and idempotent repair cannot extend it. Before expiry replay remains stable even
after balance changes. After safe expiry the same key can create a new generation and
a new payment. Clients requiring permanent deduplication must not reuse expired keys.

Processing expiry never authorizes blind takeover. A finite ownership lease prevents
premature recovery before admitted work starts; after lease expiry the guard establishes
whether work is active, committed or safely fenceable. A busy B transaction cannot be
replaced, regardless of TTL. Uncertainty stays 409 pending reconciliation, or safe 503
for infrastructure failure, without claiming rollback.

An explicit bounded maintenance command reconciles abandoned processing and removes safely
expired resolved generations. It runs sequentially with the same declared connection
allocation; deployments must schedule it and monitor unresolved age. There is no implicit
always-running service worker, infinite retry, or exact-to-the-second deletion guarantee.
Unresolved rows may outlive 24 hours to preserve financial safety; this is not indefinite
response-cache retention. Logs and metrics expose unresolved state and maintenance failure.

## Resources and async ownership

Retain configured connection allocation C, process count P including surge, and
P * pool_max <= C, where pool_max bounds the single per-instance pool. Its native
queued-acquisition bound applies to every checkout. Pool min/max/queue and establishment/acquisition/SQL/lock/idle/
transaction waits remain finite. Local settings are illustrative, not production capacity.
The same absolute request deadline covers A claim, B work, A publication and bounded
cleanup; each phase uses the remaining implicit asyncio timeout rather than a fresh full
budget. Server deadlines are independent defenses, not an end-to-end SLA.

Preserve supervised async task ownership, shielded cleanup and the synchronous
BORROWED-to-POOL_OWNED return handoff. Before handoff, cleanup may discard its owned
session; after handoff it cannot close a session reassigned to another request. Join
cancelled work; never leave detached financial work or reset a cancelled caller's deadline.
Requests must return processing/conflict without acquiring B. Retry policy remains caller-
controlled, with no hidden unbounded retry loop.

## Logging, tracing and settings

Dedicated structured logging captures context at each call before enqueue. Every HTTP
attempt has an effective X-Correlation-ID and fresh X-Request-ID; W3C trace IDs remain
separate. Context propagates through controller, service, storage and cleanup, resets on
exit, and never appears as metric labels or idempotency identity. App-scoped sinks avoid
global handler mutation or cross-app leakage. Startup events have no request context.

INFO logs request arrival/completion for all app HTTP routes, including probes; WARNING/
ERROR records operational failures once at the responsible boundary. DEBUG identifies each
processing stage/storage operation and per-payment ordinal, excluding financial data and
raw SQL/driver text at every level. Bounded nonblocking queues and finite shutdown retain
drop metrics/degradation reporting. Log loss is possible and must not affect payments.

Settings derives from pydantic_settings.BaseSettings. Load during lifespan, validate once,
then inject immutable settings into logger, telemetry and pool. Preserve named environment
variables, explicit configuration precedence, literal dotenv, isolated test mappings and
configured-profile budget requirements. Migrations/maintenance share validated sources
without constructing HTTP resources. api.md and code-design.md define exact source policy.

Metrics retain finite traffic/outcome/latency/pool/failure dimensions. Processing conflicts
are distinct from changed-payload conflicts. Payment-created counters count only the
acknowledged new B commit, never replay, recovery publication or a mere write attempt.
Operational counters are not a ledger and can miss crash-window commits.
Readiness remains read-only and bounded; liveness/metrics have no financial SQL. Probe
logging does not turn probes into payment-attempt metrics. Trace/export failures are isolated.

## Deployment and migration

Deliver the new Alembic revision and supported-head validation in the non-root production
image first. Do not edit the old initial revision to rewrite applied history. Preserve old
rows, ordered response bodies, balances, payment IDs and sequence state. Legacy completed
entries get 24 hours from one transactional migration cutover timestamp; never expire all
old keys on deploy. Incomplete legacy records fail migration rather than become new work.
Old app instances must be stopped/drained before the new stateful writer starts; mixed
old/new writers are unsupported. Migration precedes startup; application requests do no DDL.
Connectivity/revision failure fails startup; migration failure rolls back changes and revision.
Downgrade that would discard receipts or state is refused before mutation.

## Risks and alternative

The dual-write crash gap is the primary risk. Deleting receipts or weakening the guard
would allow a second payment; fault-injected real PostgreSQL tests must show it cannot.
The simpler previous single-transaction approach avoids recovery bookkeeping but cannot
expose a committed processing state independently, and is superseded by this design.
Hot firms still serialize; larger pools cannot fix that. Recovery scheduling, TTL reuse,
DEBUG volume, clock-derived leases and compatibility cutover require operational discipline.
No throughput claim, external collector, authentication or production ingress policy is added.

## Verification and gates

SC-1–54 and every outline row must be bound, including changed processing/retry semantics.
Exercise independent pools, actual PostgreSQL coordination, expired-generation races,
old-owner resumption, migration preservation and faults before/after every A/B commit.
Verify safe fallback headers, context isolation, INFO/DEBUG filtering, redaction, settings
precedence, bounded logging degradation and final real container HTTP behavior.
Exact candidate-bound integration, independent complete-source review and separately
audited final acceptance remain required. This document is an unapproved postimage.

Schema structure is owned by Alembic migrations and PostgreSQL constraints. Startup checks
connectivity and the packaged supported revision; it does not reconstruct the schema from
application catalogs or certify manual schema drift. Legacy adoption alone retains a
frozen migration-only compatibility check. Schema changes must use explicit migrations.
