Feature: Atomic and idempotent bulk settlement between firms
  All amounts and balances below are US cents unless dollars are explicitly stated.
  Existing replay scenarios occur before completed TTL expiry unless stated otherwise.

  Background:
    Given firms A, B and C exist with unique canonical UUIDs
    And firm A has 200000 cents and firms B and C have 0 cents

  @SC-1
  Scenario: Pay every recipient together
    When A submits one batch paying B "1200.75" dollars and C "300" dollars
    Then the response is 201 with 2 payments_created and 150075 total_amount_cents
    And its payments list contains both persisted payments with their generated IDs in request order
    And balances of A, B and C are 49925, 120075 and 30000 cents
    And exactly 2 payment rows preserve the payer, recipients, amounts and descriptions

  @SC-2
  Scenario: Reject the entire unaffordable batch
    When A submits one batch paying B "1200" dollars and C "900" dollars
    Then the response is 422 with detail "insufficient_funds"
    And balances of A, B and C remain 200000, 0 and 0 cents
    And no payment rows are inserted

  @SC-3
  Scenario: Spend the exact balance through repeated recipients
    When A submits two separate payments of "1000" dollars to B in one batch
    Then the response is 201
    And balances of A, B and C are 0, 200000 and 0 cents
    And exactly 2 payments to B are persisted

  @SC-4
  Scenario Outline: Reject malformed or disallowed input before reserving a key
    When a batch contains <invalid input>
    Then the response is 422
    And no payment or balance changes occur
    And no idempotency key is reserved
    Examples:
      | invalid input                        |
      | an empty payments array              |
      | 1001 payment entries                 |
      | a malformed payer or payee UUID      |
      | a self-payment from A to A           |
      | a missing description                |
      | a description with 2001 characters   |
      | an unexpected JSON attribute         |
      | a missing or empty Idempotency-Key   |
      | an Idempotency-Key containing spaces |
      | an Idempotency-Key of 129 characters |

  @SC-5
  Scenario Outline: Parse dollars exactly and reject invalid money
    When A requests one payment with amount <amount>
    Then the response status is <status>
    And the payment amount is <cents> or no payment is inserted
    Examples:
      | amount         | status | cents |
      | "0.01"         | 201    | 1     |
      | "1.2"          | 201    | 120   |
      | "0001.00"      | 201    | 100   |
      | "0"            | 422    | none  |
      | "-1"           | 422    | none  |
      | "+1"           | 422    | none  |
      | "1.001"        | 422    | none  |
      | "1e2"          | 422    | none  |
      | ".1"           | 422    | none  |
      | "1."           | 422    | none  |
      | " 1"           | 422    | none  |
      | numeric JSON 1 | 422    | none  |
      | "21474836.48"  | 422    | none  |

  @SC-6
  Scenario: Reject recipient overflow including aggregated credits
    Given B has 2147483646 cents
    When A submits two payments of "0.01" dollars to B in one batch
    Then the response is 422 with detail "balance_limit_exceeded"
    And balances of A, B and C remain 200000, 2147483646 and 0 cents
    And no payment rows are inserted

  @SC-7
  Scenario: Replay a normalized successful request
    Given A completed a payment of "01.0" dollars to B using key K
    When that batch is retried with "1.00" and uppercase spellings of the same UUIDs
    And key K and every description and payment position are unchanged
    Then the response status and body equal the original 201 response
    And the returned payment IDs equal the original committed payment IDs
    And only one payment of 100 cents exists

  @SC-8
  Scenario Outline: Reject a conflicting key
    Given A completed a batch using key K
    When A reuses key K with <change>
    Then the response is 409 with detail "idempotency_key_conflict"
    And no additional payment or balance changes occur
    Examples:
      | change                       |
      | a different amount           |
      | a different payee            |
      | a different description      |
      | a different payment ordering |

  @SC-9
  Scenario: Replay a decline after the balance changes
    Given A was denied a 210000-cent batch under key K
    And A subsequently receives a top-up to 300000 cents
    When the original batch is retried under key K
    Then the original 422 response is replayed without transfers
    When the batch is submitted under a new key L
    Then the response is 201 and exactly that batch is transferred

  @SC-10
  Scenario: Scope identical key strings to different payers
    Given A and B each have 10000 cents
    When A and B each pay C 100 cents using key K
    Then both responses are 201
    And balances of A, B and C are 9900, 9900 and 200 cents
    And exactly 2 payments are persisted

  @SC-11
  Scenario: Concurrent identical retries across instances
    Given A has 10000 cents
    When overlapping identical requests through independent instances pay B 3000 cents under key K
    Then one admitted request returns 201 and duplicates observing processing return 409
    And identical retries after completed publication replay the same 201 result and persisted payment ID
    And balances of A, B and C are 7000, 3000 and 0 cents
    And exactly 1 payment is persisted

  @SC-12
  Scenario: Concurrent spending cannot overdraw a payer
    Given A has 10000 cents
    When overlapping requests through independent instances attempt to spend more than A's balance under distinct keys
    Then every accepted batch returns 201 and every unaffordable batch returns 422
    And the total committed debit does not exceed 10000 cents
    And all balances are nonnegative and their total is conserved
    And only accepted batches have persisted payments

  @SC-13
  Scenario: Firms can pay and receive concurrently
    Given participating firms have enough funds for their outgoing batches
    When overlapping requests include transfers in opposing directions between firms
    Then every accepted batch is atomic and conserves funds
    And the requests finish with success or a bounded retryable failure
    And no request waits indefinitely because of conflicting transfer directions

  @SC-14
  Scenario: Roll back an insert failure after balance updates
    Given an injected database failure occurs while inserting a payment
    When A submits a valid affordable batch under key K
    Then the response is 503 with Retry-After 1
    And no payments, balance changes or committed result receipt remain
    And bounded cleanup or reconciliation releases the abandoned claim before retry
    When the failure is removed and the batch is retried under key K
    Then exactly one successful batch is committed with response 201

  @SC-15
  Scenario: Roll back a lock timeout and permit retry
    Given another transaction holds a required firm lock beyond the configured lock timeout
    When A submits a valid affordable batch under key K
    Then the response is 503 with Retry-After 1
    And no payments, balance changes or committed result receipt remain
    And bounded cleanup or reconciliation releases the abandoned claim before retry
    When the lock is released and the same batch is retried under key K
    Then exactly one successful batch is committed with response 201

  @SC-16
  Scenario: Recover after losing the successful HTTP response
    Given A's batch under key K committed but its HTTP response was lost
    When the caller retries the identical batch under key K
    Then bounded reconciliation publishes the committed receipt if necessary
    And the original 201 result is replayed after any temporary processing 409
    And only the original batch's payment and balance effects exist

  @SC-17
  Scenario: Resolve UUIDs through an index under a larger firm population
    Given a firm population representative of the documented workload assumptions
    When participating firms are resolved by UUID
    Then query-plan evidence demonstrates efficient indexed UUID lookup
    And duplicate stored UUIDs are rejected by PostgreSQL

  @SC-18
  Scenario: Run and verify the containerized service
    Given local PostgreSQL is ready and Alembic upgrade head completes
    When the Python 3.15 service image starts as a non-root user
    And the example batch is submitted over real HTTP
    Then the response is 201 and persisted balances match SC-1
    And retrying the same key replays the result without additional payments

  @SC-19
  Scenario Outline: Reject unknown firms and preserve descriptions as data
    When a batch contains <case>
    Then the response status is <status>
    And the database outcome is <outcome>
    Examples:
      | case                                  | status | outcome                             |
      | an unknown payer                      | 422    | no monetary changes                 |
      | one known and one unknown payee       | 422    | no monetary changes                 |
      | SQL-like Unicode text as description  | 201    | exact description, all tables intact |

  @SC-20
  Scenario: Bound resource usage and verify from isolated inputs
    Given a fresh isolated checkout with no pre-existing virtual environment
    When the approved setup and required checks run
    Then setup installs the locked dependencies and checks use real PostgreSQL
    And connections, queues, waits and retries have finite limits
    And those limits are justified against the documented connection budget and request deadline
    And formatting, lint and strict application/script typing pass
    And missing Docker fails the integration check instead of silently skipping it

  @SC-21
  Scenario: Pinecrest pays Nair twice and Lopez once in the supplied sample
    Given the firms table is seeded with these exact rows replacing the background balances:
      """sql
      INSERT INTO firms (id, name, balance_cents, uuid)
      VALUES (1, 'Pinecrest CPA Group', 5000000, '3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41'),
             (2, 'Lopez Bookkeeping', 50000, '8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10'),
             (3, 'Nair Tax Services', 200000, 'e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25');
      """
    And no payments have been persisted
    When POST /v1/bulk-payments is submitted with a fresh valid Idempotency-Key and this JSON body:
      """json
      {
        "payer_firm_uuid": "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41",
        "payments": [
          {
            "amount": "6250",
            "payee_firm_uuid": "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25",
            "description": "Overflow returns, August 2026"
          },
          {
            "amount": "5800.5",
            "payee_firm_uuid": "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25",
            "description": "Amended returns, August 2026"
          },
          {
            "amount": "1200.75",
            "payee_firm_uuid": "8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10",
            "description": "Bookkeeping cleanup, 3 clients"
          }
        ]
      }
      """
    Then the response is 201 with 3 payments_created and 1325125 total_amount_cents
    And its payments list contains all 3 persisted payments with their generated IDs in request order
    And the persisted firm balances are:
      | id | name                | balance_cents | balance_dollars |
      | 1  | Pinecrest CPA Group | 3674875       | 36748.75        |
      | 2  | Lopez Bookkeeping   | 170075        | 1700.75         |
      | 3  | Nair Tax Services   | 1405050       | 14050.50        |
    And exactly these 3 payments are persisted in request order:
      | payer_firm_id | payee_firm_id | amount_cents | description                      |
      | 1             | 3             | 625000       | Overflow returns, August 2026    |
      | 1             | 3             | 580050       | Amended returns, August 2026     |
      | 1             | 2             | 120075       | Bookkeeping cleanup, 3 clients   |
    And the total firm balance remains 5250000 cents

  @SC-22
  Scenario: Correlate structured request logs and traces without leaking financial data
    Given a request contains recognizable canary descriptions, identities and an idempotency key
    When the request completes through an async handler and its operation task with valid trace context
    Then X-Request-ID identifies that attempt and matches its structured completion log
    And its allowed trace and span IDs correlate the request and database phases
    And emitted logs, spans and metric labels exclude the canaries, amounts, credentials and raw SQL
    And overlapping requests never inherit each other's request context

  @SC-23
  Scenario: Distinguish attempts from newly committed monetary effects
    When a valid affordable batch is committed and then replayed with the same key
    Then operational signals distinguish the committed attempt from the replayed attempt
    And the created-payments counter increases only for the newly committed batch
    And the response lists the same persisted payment IDs on both attempts
    And a validation error, business rejection, conflict and database failure have distinct bounded outcomes
    And metric labels contain only the documented finite label sets

  @SC-24
  Scenario: Separate process liveness, database readiness and metrics
    Given the service has completed initialization
    When PostgreSQL becomes unavailable
    Then liveness returns 200 without a database query
    And readiness returns 503 within the configured health-probe budget
    And metrics remain readable without a financial database query
    And operational probes do not change balances, create payments or reserve idempotency keys
    And readiness returns 200 after database recovery and successful bounded probing

  @SC-25
  Scenario: Preserve transfers when telemetry is degraded
    Given log and trace queues are full or their export destinations are unavailable
    When an affordable batch is submitted and retried under the same key
    Then monetary effects commit at most once and replay returns the same payment IDs
    And telemetry failures cannot change a committed result into a monetary retry
    And recording and shutdown attempts remain bounded
    And observable telemetry degradation is reported when a local signal remains available

  @SC-26
  Scenario: Boot the containerized application before delivering payment capabilities
    Given a clean checkout and an isolated local PostgreSQL instance
    When the service image is built and started after database readiness and Alembic upgrade head
    Then the image runs Python 3.15 as a non-root user and serves the HTTP application shell
    And repeating Alembic upgrade head preserves seeded firms and their balances
    And startup checks do not create payments or reserve idempotency keys
    And no payment or telemetry capability is claimed complete merely because startup succeeds

  @SC-27
  Scenario Outline: Sanitize unsupported description characters before payment processing
    Given an affordable batch whose description contains <removed_text>
    When the batch is submitted with a new idempotency key
    Then the service returns 201 with the unsupported description characters removed
    And stored descriptions and the durable response contain the sanitized text
    And retrying the sanitized request with the same key returns the same payment IDs without new effects

    Examples:
      | removed_text     |
      | U+0000           |
      | isolated U+D800  |
      | isolated U+DFFF  |
      | only U+0000      |

  @SC-28
  Scenario: Preserve supported description text and replay it exactly
    Given an affordable batch with empty, whitespace and supplementary Unicode descriptions
    When the batch is submitted and replayed with the same key
    Then each description is persisted and returned exactly with stable payment IDs

  @SC-29
  Scenario: Database waiting does not block the application event loop
    Given a payment operation awaiting withheld database replies within its configured budget
    When a concurrent liveness request is submitted
    Then liveness completes while the payment operation is still waiting
    And the expired operation returns retryable 503 and discards its borrowed connection
    And no payment task or pool waiter is left behind
    And same-key recovery creates or replays exactly one committed batch

  @SC-30
  Scenario: Legacy payment route is unavailable
    Given the standard three firms are seeded
    When a valid bulk payment is posted to the legacy unversioned route
    Then the response status is 404
    And no payments or idempotency reservations or balance changes are persisted

  @SC-31
  Scenario: Initialize an empty application database with tracked migrations
    Given an empty application database without a recorded revision
    When Alembic upgrades the database to the service head revision
    Then the supported application schema and head revision are committed together
    And no financial or idempotency records are created by migration

  @SC-32
  Scenario: Adopt a compatible legacy schema without changing its data
    Given a complete compatible legacy schema with firms, payments and durable request results
    And no Alembic revision has been recorded
    When Alembic upgrades the database to the service head revision
    Then the head revision is recorded only after compatibility verification
    And all original records, balances and generated identities are preserved
    And retained request results still replay without additional monetary effects

  @SC-33
  Scenario Outline: Reject unsupported legacy schemas without recording success
    Given a legacy application schema that is <condition>
    When Alembic attempts to upgrade the database
    Then the upgrade fails without advancing the recorded revision
    And all original application schema objects and data remain unchanged

    Examples:
      | condition                 |
      | partially present         |
      | incompatible in structure |

  @SC-34
  Scenario: Repeat a completed upgrade without changing application data
    Given the supported head revision and seeded financial records
    When Alembic upgrade head is run again
    Then the same single head revision remains recorded
    And all original records, balances and generated identities are preserved

  @SC-35
  Scenario: Serialize concurrent upgrades safely
    Given an application database eligible for upgrade
    When independent migration processes overlap while upgrading that same database
    Then successful callers observe one consistent head revision and schema
    And a caller exceeding its configured wait fails within its finite bound
    And no duplicate schema objects or financial changes are introduced

  @SC-36
  Scenario: Roll back a failed revision and retry safely
    Given an upgrade fails after changing schema but before completing revision recording
    When the failed upgrade transaction finishes
    Then neither its partial schema changes nor its new revision are committed
    And previously committed financial and request records are preserved
    When the failure is removed and Alembic upgrade head is retried
    Then exactly the supported schema and head revision are committed

  @SC-37
  Scenario: Reject a destructive initial downgrade
    Given the supported head revision and seeded financial records
    When a downgrade that would remove the initial application schema is requested
    Then the downgrade is rejected before changing application data or revision

  @SC-38
  Scenario Outline: Refuse startup against an unsupported recorded revision
    Given a structurally compatible database whose recorded revision is <revision>
    When the service attempts to start
    Then initialization fails before payment requests can be served
    And no financial or idempotency records are changed

    Examples:
      | revision |
      | missing  |
      | older    |
      | unknown  |

  @SC-39
  Scenario: Preserve a caller correlation token through the operation
    Given a valid payment request with one X-Correlation-ID header "firm-job_2026.10"
    When the request completes
    Then its response echoes that correlation ID and a fresh X-Request-ID
    And controller, service and storage events share the effective correlation and request IDs

  @SC-40
  Scenario Outline: Generate safe metadata for absent or malformed correlation headers
    Given correlation metadata is <metadata>
    When an application HTTP request returns <status>
    Then exactly one safe generated correlation ID is returned
    And raw invalid metadata is absent from logs and responses
    And the payment validation and execution decision is unaffected by correlation replacement
    Examples:
      | metadata              | status |
      | absent                | 201    |
      | empty                 | 422    |
      | invalid characters    | 409    |
      | oversized             | 503    |
      | duplicate headers     | 404    |
      | comma-combined values | 500    |
    # Also exercise each metadata form independently of status; test data is not an SLA.

  @SC-41
  Scenario: Replay is independent of diagnostic metadata
    Given a payment committed under a key with correlation ID "attempt-one"
    When the same normalized request and key use correlation ID "attempt-two"
    Then the original status, payment list and IDs are replayed without new monetary effects
    And the response and logs use "attempt-two" and a fresh request ID

  @SC-42
  Scenario: INFO provides operational request visibility
    Given the log level is INFO and the sink is healthy
    When requests complete with success, rejection, conflict and infrastructure failure
    Then each application request has one arrival and one completion event with status and outcome
    And infrastructure errors have a classified warning or error without duplicated exception reports
    And routine processing-stage events are suppressed

  @SC-43
  Scenario: DEBUG follows each meaningful stage without financial payloads
    Given the log level is DEBUG and the sink is healthy
    When a batch executes through controller, idempotency, locks, planning, writes and commit
    Then structured start and finish or failure events identify each processing stage and storage operation
    And per-payment insertion events identify only progress ordinal rather than financial identifiers
    And all levels exclude financial payloads, credentials, SQL parameters and raw exception text
    And an attempted write is not reported as committed before commit acknowledgement

  @SC-44
  Scenario: Context remains isolated through failures and cancellation
    Given overlapping requests on independent app instances include distinct correlation IDs
    When one times out, one is cancelled and others complete
    Then their handler, service, storage and cleanup events retain their own request context
    And context is reset before subsequent requests and background lifecycle events
    And unknown-commit and rollback failures retain truthful safe classifications

  @SC-45
  Scenario: Typed configuration preserves deliberate sources
    Given conflicting values in explicit configuration, process environment and literal dotenv
    When normal settings are loaded
    Then explicit configuration precedes environment, which precedes dotenv and permitted defaults
    And configured-profile budgets remain required and secrets are never logged
    When settings are constructed from an explicit test mapping
    Then ambient environment and dotenv cannot supply missing values or override the mapping

  @SC-46
  Scenario Outline: Reject invalid configuration before serving traffic
    Given configuration contains <problem>
    When the service starts
    Then startup fails with a safe diagnostic and no financial changes
    Examples:
      | problem                              |
      | unsupported log level                |
      | missing configured deployment budget |
      | inconsistent database timeout bounds |
      | invalid queue bound or sample ratio  |
    # Valid INFO/DEBUG and higher thresholds must also demonstrate actual filtering.

  @SC-47
  Scenario: Degraded logging cannot decide payments
    Given a slow or failing log sink and a saturated bounded queue
    When payment requests execute with verbose logging
    Then monetary and idempotency invariants remain intact within existing operation budgets
    And requests do not wait for the external sink
    And telemetry drop signals expose loss without recursive failure logging

  @SC-48
  Scenario: Middleware admits new requests and short-circuits known entries
    Given a validated normalized request and idempotency key
    When middleware resolves the request entry using connection A
    Then a new entry is committed with status processing before controller admission
    And a matching completed entry returns its cached status and body without invoking the controller
    And a matching processing entry returns 409 request_processing without invoking the controller
    And a different fingerprint returns 409 idempotency_key_conflict
    And neither the lightweight checker nor the payment service calls the other

  @SC-49
  Scenario: Overlapping requests observe processing without repeating business work
    Given independent app instances receive the same key and normalized request
    When one commits its processing claim on A and performs financial work on B
    Then competing requests observe actual database coordination and return 409 while it is processing
    And exactly one business execution commits with an atomic result receipt
    And subsequent requests replay the completed response after publication on A
    And no request holds a connection A transaction while waiting for connection B

  @SC-50
  Scenario: Corrupt cached responses cannot authorize financial execution
    Given a completed request entry has a corrupt cached response
    When middleware resolves the same request
    Then it returns a safe infrastructure failure instead of invoking payment execution
    And no monetary effects occur

  @SC-51
  Scenario: Expired completed keys have an explicit reuse boundary
    Given a completed response is retained for 24 hours from completion using database time
    When an identical request arrives before expiry
    Then the original response is replayed
    When a request arrives at or after expiry and the old generation is safely resolved
    Then an atomic fresh generation may execute a new payment
    And bounded cleanup does not delete unresolved guards or receipts

  @SC-52
  Scenario: Recover a commit before completed publication
    Given B committed financial changes and its result receipt
    And the process crashed before A published completed
    When a retry or reconciler resolves the processing entry
    Then the committed receipt is published and replayed without executing payments again
    And a temporary unresolved processing state returns 409
    And a publication failure does not claim that financial changes rolled back

  @SC-53
  Scenario: Recover an abandoned claim without allowing stale ownership
    Given A committed processing but its owner crashed before B committed
    When recovery acquires the execution guard and establishes no committed receipt
    Then it fences the old owner before admitting a replacement execution
    And an old owner that resumes cannot execute or finalize the new generation
    And failure of result-receipt recording rolls back financial effects on B

  @SC-54
  Scenario: TTL and failures cannot bypass an active or uncertain business transaction
    Given a processing entry reaches its 24-hour expiry with an active or uncertain B transaction
    When cleanup and duplicate requests race with recovery and the old owner
    Then they cannot delete the execution guard or authorize a second financial execution
    And processing returns 409 until bounded reconciliation establishes the result
    And exhausted database budgets return the existing safe 503 without hidden retries
    And correlation and sanitized logs distinguish claim, execution, publication and recovery

  @SC-55
  Scenario: Migrate existing completed keys with a full retention window
    Given the old supported schema contains committed success and business-rejection results
    When the lifecycle revision upgrades at one database cutover timestamp
    Then original responses, payment IDs, balances, rows and sequence identities are preserved
    And each old key becomes completed with a guard and a corresponding result receipt
    And its expiry is exactly 24 hours after cutover rather than its old creation timestamp
    And incomplete or invalid legacy results fail without advancing revision or changing data
    And startup refuses the older revision until upgrade succeeds

  @SC-56
  Scenario: Reconciliation and cleanup are bounded and financial-data safe
    Given maintenance candidates include expired completed, abandoned processing, active and corrupt entries
    When the explicit maintenance command runs with finite batch, batch-count and total-time bounds
    Then it publishes receipts, fences no-effect abandoned claims and deletes only safely expired generations
    And active guards are skipped without unbounded waits or spinning
    And corrupt or unresolved records are retained and operationally signaled without financial execution
    And no financial rows or balances change and unrelated generations remain intact

  @SC-57
  Scenario: Independent transactions share one request budget and bounded pool
    Given an admitted request uses successive lifecycle and business checkouts from one bounded pool
    When each phase is observed through independent database transactions
    Then physical backend reuse is permitted between independent transactions
    And A is released before B checkout and B is released before publication checkout
    And native connection and queued-acquisition counts stay within the declared allocation
    And no transaction or row lock spans phases and no checkout creates a new operation budget
    And timeout or cancellation at every boundary joins owned work and preserves existing pool handoff rules
    And exhausted capacity returns bounded safe failure without stranding pool capacity or duplicating effects
    And any remaining processing entry is retained for safe bounded recovery

  @SC-58
  Scenario: Alembic owns schema integrity and startup checks its supported revision
    Given the database was upgraded through Alembic
    When the service starts against the supported revision
    Then startup checks revision without reconstructing application schema catalogs
    And migration-created constraints reject invalid balances and duplicate firm UUIDs


  @SC-59
  Scenario Outline: Raw JSON decoder boundaries reject safely before admission
    Given a raw decoder boundary payload "<kind>"
    When the raw decoder boundary request is submitted
    Then safe 422 diagnostic metadata is returned without money or admission changes

    Examples:
      | kind                     |
      | integer conversion limit |
      | excessive nesting        |
