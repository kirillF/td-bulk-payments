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
| FR-5 | Require a case-sensitive `Idempotency-Key` scoped to the payer. An identical normalized request under that key executes at most once and replays the original status and response, including across instances and after a lost response. |
| FR-6 | Reusing a payer/key with different normalized content returns 409 without additional monetary effects. Money strings and UUIDs are normalized; JSON object order is irrelevant, while payment list order and exact descriptions are significant. Different payers may use the same key independently. |
| FR-7 | Persist business rejections, including insufficient-funds 422, for replay. A later top-up does not change that key's outcome; a new attempt needs a new key. No automatic key expiration is provided. |
| FR-8 | SQL failure or timeout rolls back all uncommitted effects and the key reservation, returns 503 with `Retry-After: 1`, and permits retry with the same key. If commit acknowledgement is lost, replay or one new execution must resolve the uncertainty safely. A 503 cannot promise that a commit never occurred. |
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
| FR-13 | Service startup requires both a compatible application schema and the revision supported by that service version. A missing, older or unknown revision prevents startup; a healthy database connection alone cannot waive this check. |
| FR-14 | Initial migration adoption does not authorize destructive schema repair, data reset or downgrade. Unsupported destructive downgrade requests fail without changing application data or the recorded revision. |

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
- INV-4: One payer/key produces at most one committed set of monetary effects and a stable
  durable result. Changed content cannot silently reuse that key.
- INV-5: Concurrent requests cannot approve spending against stale unlocked balances;
  process-local state is never the authority for funds or idempotency.
- INV-6: Malformed input, unknown firms, self-payments and overflow cannot produce partial
  monetary effects. Description text is stored as data, never executed as SQL.

- INV-7: An applied schema revision and its schema changes commit atomically. Rejected or
  failed migration attempts cannot falsely advance the revision or damage existing
  financial/idempotency data. Concurrent upgrade callers cannot bypass this invariant.

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

## Requirement-driven verification status

SC-1–30 (59 expanded cases) describe the current pre-Alembic application contract.
SC-31–38 add migration acceptance obligations derived from FR-10–14 and NFR-8.
The aligned target has38 scenarios/70 expanded cases. These additional obligations are
planned; neither their specification nor earlier PASS records proves Alembic delivery.
