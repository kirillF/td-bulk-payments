# Firm Payments — product requirements

## Outcome and scope

Allow one firm to settle a batch of debts to other firms using its platform balance.
A valid, affordable batch persists every payment and every balance change atomically.
An unaffordable batch returns 422 without monetary effects. The service must remain
correct across concurrent requests to multiple load-balanced instances.

Scope includes one JSON HTTP endpoint, PostgreSQL persistence, durable idempotency,
indexed firm UUID lookup, containerized local execution, versioned schema migrations and executable verification.
Python 3.15, FastAPI, Pydantic and explicit SQL are required; no ORM.

Authentication, external payment processors, currency conversion, automatic deposits,
payment reversal, history/search endpoints, cloud deployment and real-money operational
qualification are outside this take-home. There is no merge/publication authority here.
A production caller must be authorized to act for its payer; the exercise trusts the payer
identity in the request. Platform balance initialization is external to the payment API.

## Functional requirements

| ID | Requirement |
| --- | --- |
| FR-1 | Accept `POST /v1/bulk-payments` with `payer_firm_uuid` and a nonempty `payments` array. Each entry has a positive dollar-string `amount`, `payee_firm_uuid`, and string `description`. UUIDs must parse; reject unknown attributes and numeric JSON amounts. Validation failure returns 422 without reserving an idempotency key. |
| FR-2 | Resolve every involved firm. Unknown payer or payee returns 422 with no monetary changes. Self-payments return 422. Repeated payees are allowed; persist every entry and aggregate its credits. |
| FR-3 | Compare the payer's locked balance with the sum of the entire batch. Insufficient funds denies the entire batch with 422. Spending the exact available balance is permitted. |
| FR-4 | For an accepted batch, insert all payments with correct payer/payee IDs, cents and descriptions, debit the payer once by the total, credit all recipients, commit, and return 201. The response reports `payments_created`, `total_amount_cents`, and the complete list of created payments with their generated IDs, public payer/payee UUIDs, exact cents and descriptions in request order. Idempotent replay preserves that list and its IDs. |
| FR-5 | Require a case-sensitive Idempotency-Key scoped to the payer. During one retained request generation at most one monetary execution commits. Completed identical requests replay their original status/body/IDs across instances; processing identical requests return 409 without execution. |
| FR-6 | Different normalized content under an unexpired payer/key returns 409 without effects. Normalize money and UUIDs; JSON object order is irrelevant, payment order and sanitized descriptions significant. Different payers have independent key namespaces. After safe expiry, a new generation may use the key. |
| FR-7 | Persist business rejections for stable replay despite later balance changes. Processing entries expire after 24 hours from creation; completed cache entries after 24 hours from first completion publication, using database time. Replays do not extend TTL. Safe expired-key reuse is a new attempt and may pay again; unresolved active/uncertain operations cannot be erased or repeated by TTL. |
| FR-8 | Infrastructure failure returns safe 503 with Retry-After: 1 and no uncommitted monetary effects. A separately committed processing claim may remain until bounded recovery. If B committed, failure to publish completed on A must not permit duplicate effects. Same-key recovery resolves the durable result or fences an abandoned no-effect owner before another execution; 503 never promises commit did not occur. |
| FR-9 | Use exact integer-cent arithmetic and reject malformed amounts or any individual amount/resulting balance outside the supplied signed 32-bit INTEGER range. Reject recipient overflow with 422 and no monetary changes. No rounding or binary floating-point conversion. |

## Schema lifecycle requirements

These requirements describe deployment outcomes and data safety. Architecture selects the
migration mechanism; scenario counts, test fixtures and implementation helpers are not
product requirements.

| ID | Requirement |
| --- | --- |
| FR-10 | Apply tracked, ordered Alembic revisions to create an empty application database and record the applied revision. Schema changes run explicitly before service startup, never within payment requests. |
| FR-11 | Adopt a database created by the former bootstrap only after verifying its complete supported schema. Preserve firms, balances, payments, generated identities and durable idempotency results. Reject incompatible or partial legacy schemas without falsely recording migration success. |
| FR-12 | Repeated and concurrent upgrade requests must produce one consistent supported schema/revision state without duplicate DDL or data loss. If an upgrade fails, its schema and revision changes roll back together and a later retry can safely proceed. |
| FR-13 | Service startup requires connectivity and the Alembic revision supported by that service version. A missing, older or unknown revision prevents startup; a healthy database connection alone cannot waive this check. |
| FR-14 | Initial migration adoption does not authorize destructive schema repair, data reset or downgrade. Unsupported destructive downgrade requests fail without changing application data or the recorded revision. |

## Correlation, logging, configuration and request lifecycle

These obligations extend operational visibility and separate middleware coordination from
monetary logic. Concrete protocol values and representation belong in architecture/program
design, not requirements inferred from tests.

| ID | Requirement |
| --- | --- |
| FR-15 | Accept an optional case-insensitive HTTP `X-Correlation-ID` header. Preserve one valid supplied value and return it in the response. Generate a value when absent. Apply this to application-handled HTTP requests, including validation failures, unknown routes and sanitized server errors. |
| FR-16 | Propagate the effective correlation ID through controller, services and database operations, including owned async work and cleanup. Isolate concurrent requests and reset context after completion. Retain a fresh server-generated `X-Request-ID` for every attempt and existing W3C trace semantics. |
| FR-17 | Correlation metadata does not affect request normalization, idempotency identity, stored response bodies or monetary decisions. Replays use the current attempt's headers and retain original payment IDs/body. |
| FR-18 | Idempotency middleware claims or resolves request entries on connection A. It returns cached completed responses or 409 for processing entries; only a newly claimed request reaches the controller. The controller orchestrates monetary execution on connection B. Neither the checker nor payment service calls the other. |
| FR-19 | An atomic claim creates a processing entry. Completed entries replay their validated cached result; changed fingerprints return 409. Entries have the 24-hour retention and safe expiry semantics in FR-7. Dual-connection failure must never repeat a committed monetary operation. Corrupt or unresolved results fail closed. |
| NFR-9 | A dedicated structured application logger emits request arrival and completion/status at INFO by default, plus classified operational failures at WARNING/ERROR. It provides DEBUG events for each processing stage, including storage operations, with consistent correlation. Expected business/validation rejection is an outcome, not an internal error. |
| NFR-10 | Log severity threshold is deployment-configurable. DEBUG enables verbose processing visibility; INFO avoids routine processing-step noise. All levels exclude bodies, descriptions, monetary values, firm/payment identifiers, raw idempotency keys, credentials, raw SQL/parameters and raw driver exception text. Correlation IDs are untrusted operational metadata, never authority. |
| NFR-11 | Configuration is represented and validated by Pydantic BaseSettings at startup, including database budgets, logging and telemetry. Preserve existing environment names, environment-over-local-dotenv precedence, literal dotenv values and explicit configured-profile budgets. Invalid configuration fails before serving requests, without exposing secrets. Explicit test configuration must not read ambient configuration. |
| NFR-12 | Logging has bounded queues, nonblocking request-side submission and finite shutdown. Failure, saturation or filtering must not change transaction outcomes or operation budgets. Report dropped telemetry through the existing operational signals. Correlation IDs must not become metric labels. |

## Proposed limits and validation details

These are explicit product choices for review, not throughput claims:

- Batch size: 1–1,000 entries.
- Description: required string. Strip U+0000 and isolated surrogate code points U+D800–U+DFFF, then enforce the 0–2,000-character limit. Preserve all other characters exactly. Unsupported description characters are removed without a validation error; an empty sanitized description is valid. Fingerprinting, storage, responses and replay use the sanitized description.
- Amount: string of at most 32 characters, ASCII digits with an optional decimal point
  followed by one or two digits. Leading zeroes are permitted. No signs, whitespace,
  exponents, bare decimal points, zero or negative amounts.
- Maximum individual amount and resulting balance: 2,147,483,647 cents ($21,474,836.47),
  preserving the exercise's `INTEGER` columns. Batch totals use unbounded Python integer
  arithmetic and cannot bypass the payer's balance check.
- Key: 1–128 visible ASCII characters, without spaces. Missing/invalid keys return 422.
- A valid-but-unknown firm is a business rejection and may have a durable 422 result.
  Malformed requests and self-payments fail validation without reserving a key.
- Firm UUID storage uses canonical lowercase text. Existing noncanonical/duplicate UUID
  imports require explicit data migration rather than ambiguous lookups.

## Non-functional requirements

| ID | Measurable obligation and verification oracle |
| --- | --- |
| NFR-1 | Firm UUID lookup must be indexed and unambiguous, and remain efficient as the firm population grows. The verification plan must justify representative data volumes and examine database query plans; no throughput or latency SLA is assumed. |
| NFR-2 | Concurrent requests across independent service instances must preserve atomicity, prevent overdrafts, and enforce idempotency. Verification must establish overlapping execution and cover competing spending, identical retries, and opposing transfers. |
| NFR-3 | Database connections, queues, waits, and retries must be bounded. Specific values are architecture-stage proposals, justified against the deployment connection budget and request deadline. |
| NFR-4 | Reproducible Python dependency lock; formatting/lint and strict application/script typing checks. Integration/Gherkin checks use real PostgreSQL 17 through Testcontainers and fail if Docker is unavailable. Fresh-input verification must establish that ignored/generated prerequisites can be reproduced. Execution follows the audited project-local boundary; native isolation is not claimed. |
| NFR-5 | Service image uses Python 3.15 and a non-root UID. Compose starts PostgreSQL, applies versioned migrations, then starts the service. Verify a built Linux container with real HTTP and persisted database effects. Local-only credentials/ports are documented. |
| NFR-6 | Emit machine-readable structured operational logs with request correlation and distinguish successful transfers, replays, business/validation rejections, conflicts, transient failures and uncertain commit outcomes. Exclude credentials, raw keys, financial payloads and sensitive identities. Verify event structure, correlation and redaction. |
| NFR-7 | Expose bounded-cardinality metrics for traffic/outcomes, latency, database/pool contention and failures; provide correlated traces and separate liveness/readiness signals. Instrumentation must not change monetary outcomes, block transactions on external telemetry, or claim operational metrics are an authoritative financial ledger. Verify signal semantics, dependency failure and telemetry isolation. |
| NFR-8 | Migration execution has finite connection, lock and statement waits, reads runtime configuration without exposing credentials, and is reproducible from the locked production dependencies and packaged revision resources. Migration concurrency and failure verification uses real PostgreSQL. |

Concurrency counts, data-set sizes, pool sizes and timeout values are not product
requirements inferred from an existing implementation or test. Architecture must state
its workload/deployment assumptions and propose operational values. Verification design
must select and justify test parameters independently against these requirements.

Bounded fields are not a transport-level body limit. Ingress body/rate limits, secrets,
authorization, monitoring, reconciliation, backups and retention operations must be supplied
before external production exposure. A migration is not authority to silently repair an
unknown schema. Future schema/fingerprint evolution requires an explicit compatibility plan.

## Invariants

- INV-1: A batch commits every required payment and balance effect, or none.
- INV-2: Successful transfers conserve the total balance of participating firms; all firm
  balances remain nonnegative and within INTEGER range.
- INV-3: Money representation is exact integer cents throughout validation and persistence.
- INV-4: One retained payer/key generation produces at most one committed set of monetary
  effects and a stable durable result. Changed content cannot reuse an unexpired key.
  Safely expired key reuse starts a new generation, not a replay guarantee.
- INV-5: Concurrent requests cannot approve spending against stale unlocked balances;
  process-local state is never the authority for funds or idempotency.
- INV-6: Malformed input, unknown firms, self-payments and overflow cannot produce partial
  monetary effects. Description text is stored as data, never executed as SQL.

- INV-7: An applied schema revision and its schema changes commit atomically. Rejected or
  failed migration attempts cannot falsely advance the revision or damage existing
  financial/idempotency data. Concurrent upgrade callers cannot bypass this invariant.

- INV-8: A processing claim, expiry, owner crash or incomplete cache publication cannot
  authorize a second execution of an unresolved generation. A business result receipt
  commits atomically with its effects; stale owners are fenced before safe replacement.

## Acceptance and verification mapping

The accompanying `scenarios.feature` is the product acceptance contract. During slice
planning, every ID below must map to an owned executable check, including negative cases.
Existing candidate tests do not by themselves establish formal delivery or acceptance.

| Scenarios | Required obligations |
| --- | --- |
| SC-1, SC-2, SC-3 | FR-1–4, FR-9; INV-1–3, INV-6 |
| SC-4, SC-5, SC-6 | FR-1–2, FR-9; INV-1, INV-3, INV-6 |
| SC-7, SC-8, SC-9, SC-10 | FR-5–7; INV-4 |
| SC-11, SC-12, SC-13 | FR-3–6; NFR-2; INV-1–5 |
| SC-14, SC-15, SC-16 | FR-8; NFR-3; INV-1, INV-4 |
| SC-17 | NFR-1 |
| SC-18 | FR-4; NFR-5; INV-1–4 |
| SC-19 | FR-2, FR-4; INV-6 |
| SC-20 | NFR-3–4 |
| SC-21 | FR-1–5, FR-9; INV-1–4, INV-6; supplied Pinecrest/Lopez/Nair sample with repeated payee and mixed decimal precision |
| SC-22, SC-23 | NFR-6–7; INV-1, INV-4, INV-6 |
| SC-24, SC-25 | NFR-7; INV-1, INV-4 |
| SC-26 | NFR-4–5; INV-1, INV-6; independently verifiable container/bootstrap prerequisite before payment delivery |
| SC-27, SC-28 | FR-1, FR-4–5; INV-1, INV-3–4; sanitize unsupported description characters before validation/fingerprinting and preserve supported text/replay. |
| SC-29 | NFR-3, NFR-7; bounded database waiting preserves event-loop responsiveness and safe recovery. |
| SC-30 | FR-1; only the explicit /v1 payment route exists; legacy requests have no effects. |
| SC-31 | FR-10; NFR-4–5, NFR-8; INV-7; initialize an empty database with a recorded revision. |
| SC-32 | FR-11; NFR-8; INV-1–4, INV-7; adopt a verified legacy schema without changing financial data. |
| SC-33 | FR-11; NFR-8; INV-7; reject partial/incompatible legacy schemas without stamping success. |
| SC-34, SC-35 | FR-12; NFR-8; INV-7; repeated and concurrent upgrades converge safely. |
| SC-36 | FR-12; NFR-8; INV-7; failed revision changes roll back and retry safely. |
| SC-37 | FR-14; INV-7; unsupported destructive downgrade preserves state. |
| SC-38 | FR-13; NFR-5, NFR-8; unsupported or missing revision prevents service startup. |

The architecture stage must resolve how the selected transaction, data constraints,
lock ordering and idempotency representation enforce these obligations. The program
and slice stages must define signatures, exact checks, ownership and finite execution
allowances before the audited feature approval authorizes execution.

Additional scenario mapping:

| Scenarios | Required obligations |
| --- | --- |
| SC-39–41 | FR-15–17; NFR-6; correlation header behavior and replay independence. |
| SC-42–44 | FR-16; NFR-9–10; INFO/DEBUG/error visibility and isolation/redaction. |
| SC-45–46 | NFR-11; settings precedence, isolated construction and safe startup validation. |
| SC-47 | NFR-12; bounded logging degradation preserves financial outcomes. |
| SC-48–54 | FR-5–8, FR-18–19; INV-4, INV-8; middleware states, A/B crash recovery, TTL and fencing. |
| SC-55 | FR-10–13, FR-19; INV-7–8; migrate old completed records without immediate expiry/data loss. |
| SC-56–57 | NFR-3, NFR-11; FR-8, FR-18–19; INV-8; bounded maintenance and sequential A/B resource ownership. |

| SC-58 | FR-11, FR-13; NFR-3; revision-only startup and migration-owned integrity. |

## Requirement-driven verification status

The current source implements the earlier Alembic contract; earlier PASS evidence remains
bound to that earlier candidate. This target changes FR-5–8 and INV-4, adds FR-15–19,
NFR-9–12 and INV-8, updates affected prior scenarios and adds SC-39–58. It contains
59 scenarios / 100 expanded cases. No new behavior or check is delivered by this draft.

Schema authority: Alembic migrations define tables, constraints and indexes; PostgreSQL
enforces data integrity. Startup verifies connectivity and supported revision without
reconstructing application schema catalogs. Manual drift with an unchanged revision is
outside this startup guarantee. Verified legacy adoption remains migration-only.
Lifecycle, business and publication use independent successive transactions from one
bounded pool, with physical session reuse permitted and pool_max=1 supported.

| SC-59 | Existing FR-1, FR-15, FR-17; INV-6: raw decoder boundary failures return safe422 with diagnostic metadata before admission or financial changes. Payload dimensions are test-profile values, not product limits. |
