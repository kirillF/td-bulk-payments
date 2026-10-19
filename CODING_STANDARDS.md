# Coding standards

Use the approved requirements, architecture, API, code design and slices under
`docs/features/bulk-payments/`, together with their audited amendments, for feature-specific
behavior. Keep approval history in the harness records. These standards define how to
design and change code; they do not duplicate response codes, retention policies or a
particular transaction protocol. An approved target design is not evidence of implementation.

Use real PostgreSQL via Testcontainers for executable Gherkin/API journeys and SQL/concurrency boundaries. Verify all-or-nothing persistence, exact-balance and insufficient-funds outcomes, concurrent competing spending, repeated recipients, overflow, invalid requests, unknown firms, self-payment rejection, key conflicts, simultaneous retries, replay including 422, and rollback. Selective unit tests for parsing/fingerprinting. Foundation scaffold alone implements no payment behavior and claims no payment verification.

Keep interfaces explicit, changes within approved ownership, and verification tied to requirement/invariant/scenario IDs. Use E2E for journeys, integration checks for boundaries and selective unit/property tests for complex logic. Define observable success, including durable effects and non-functional thresholds where relevant. Run fast deterministic focused checks during edits and comprehensive checks before integration; preserve concise failure evidence. Inspect generated tests for weakened assertions and plausible incorrect implementations that would still pass. Differential checks are useful for migrations.

## Code design and organization

Rule of thumb: make the smallest change that restores the required behavior and passes the required checks. Preserve existing work, approvals, attempt history and safety invariants. Reuse the current path; add abstractions, workflow stages or configuration only when the concrete fix needs them.

Follow approved project requirements/design and stack conventions. These defaults guide choices within that scope; consequential conflicts require explicit HITL. Choose the simplest complete solution to current requirements; add abstractions, dependencies, options or optimizations only for demonstrated needs.

- **Modules:** hide cohesive decisions, representation and invariants behind small useful interfaces. Keep related knowledge together. Extract helpers that remove a meaningful cognitive burden; combine fragments that require reading each other's internals. Avoid pass-through layers and arbitrary size limits.
- **Readability:** use precise domain names, consistent terminology and explicit units. Keep control flow straightforward, nesting modest and state changes visible. Use types to clarify contracts; comments explain invariants, rationale and non-obvious constraints rather than syntax.
- **Contracts and failures:** make inputs, outputs, ownership, side effects and errors clear. Prefer valid-by-construction data and owned recovery; preserve validation, meaningful failures, observability and fail-closed approval/security boundaries.
- **Layout:** start with the language/framework's conventional, smallest useful structure. Keep small projects simple; group growing code by cohesive capability so related changes stay together. Separate business rules from UI, transport and persistence details proportionately; simple functions/modules often suffice. Do not create empty layers or speculative adapters.
- **Dependencies:** expose small public APIs, keep internals private and architectural dependencies directed. Avoid cycles and importing another module's internals; use existing boundary checks where available.
- **Ownership:** keep types, validation and configuration with their behavior. Extract shared code for a coherent responsibility with real consumers; avoid catch-all utility/manager dumping grounds. Similar-looking code need not share an abstraction.
- **Tests and artifacts:** follow the stack's test layout, group tests by capability where useful and keep fixtures near their tests. Separate generated code, build outputs and caches from maintained source.

Before delivery, inspect the changed path for unnecessary concepts, special cases and duplicated decisions. Simplify within approved ownership and verify with task checks. Explain non-obvious tradeoffs in existing change/task notes; no extra report, metrics or refactoring stage. Changes to approved architecture, public contracts, requirements or invariants require explicit HITL before affected implementation.

Basis: [Ousterhout](https://github.com/johnousterhout/aposd-vs-clean-code), [YAGNI](https://martinfowler.com/bliki/Yagni.html) and [Fowler on modular organization](https://martinfowler.com/bliki/PresentationDomainDataLayering.html). Adapted guidance, not quotations.

## Responsibility mapping and SRP

Before changing a use case, identify who validates input, admits execution, orchestrates
work, applies business rules, persists state, owns transactions and translates errors.
Use the following default ownership and record any feature-specific variation in its design:

| Boundary | Owns |
| --- | --- |
| Schemas | Input/output shape, field validation, explicitly approved sanitization and canonicalization. |
| Middleware | Cross-cutting request lifecycle, context and admission/short-circuit behavior. |
| Controller | Use-case orchestration and HTTP/protocol mapping; delegates transaction lifecycle to the database boundary. |
| Business service | Cohesive domain rules and business workflow through explicit storage interfaces. |
| Storage helpers | Parameterized SQL, persistence constraints and storage-specific result handling. |
| Database boundary | Connections, transaction completion, deadlines, cancellation and cleanup. |
| Application lifespan | Validated settings, dependency construction and resource startup/shutdown. |

Apply SRP by grouping decisions that change for the same reason, rather than by making
every function short. Decompose a transfer into understandable planning and persistence
steps without moving HTTP, idempotency or resource lifecycle into the payment service.
Keep pure calculations separate from I/O when that makes their invariants easier to verify.

Keep independent capabilities independent: idempotency checks decide admission/replay;
payment services decide monetary effects. Their imports and interfaces must reflect that
separation. A callback that lets a checker execute payments still couples the capabilities.
Return typed decisions with explicit new, processing, completed or conflict cases as the
contract requires; let the appropriate outer boundary choose the next action. Separate
checking, result recording and recovery when they own different decisions. Add a wrapper
or interface only when it hides a real invariant or dependency, not merely another call.

## State, transactions and asynchronous operations

Design persistent state transitions before splitting work across connections. Identify
the authoritative result, atomic effects, ownership and allowed transitions; distinguish
attempted work, acknowledged commit, cached publication and an uncertain outcome.
For independently committed phases, specify every crash window, durable recovery evidence
and stale-owner protection before implementation. A timeout or TTL alone cannot prove
that prior work failed or authorize another execution. Define retry/reuse semantics and
safe cleanup together with retention; keep maintenance bounded and owned.

Keep transaction lifecycle in the database boundary and use one absolute operation budget
across awaited phases. Prefer implicit timeout scopes to manually threading remaining-time
calculations through business code. Retain independent server-side limits. Specify resource
handoff and cancellation cleanup so cancelled callers cannot leak work, strand capacity or
close a connection already returned to another caller. Allocate connection and waiter
budgets across all pools/processes; creating a second pool does not create extra capacity.

## Validation, configuration and observability

Validate and normalize at the schema boundary, then use the same prepared values for
fingerprinting, execution, storage and responses. Sanitize only explicitly designated
fields and characters; preserve other valid input. Business services return domain results,
and protocol boundaries select HTTP responses. Persisted replay results require validation
and fail-closed handling when corrupt; incomplete metadata must not imply new work.

Represent application configuration with Pydantic BaseSettings, load it at startup and
inject it into consumers. Define source precedence, aliases, units and cross-field bounds
in one place. Explicit test configuration bypasses ambient sources. Preserve literal dotenv
behavior and existing settings semantics deliberately when refactoring; report invalid
configuration without raw secret-bearing values.

Treat operational visibility as part of the design. A dedicated structured logger captures
request context before asynchronous export and carries it through controllers, services,
storage and cleanup. Keep caller correlation, per-attempt request identity, tracing and
idempotency identity distinct. Define configurable severity: INFO for essential lifecycle
visibility, classified failures at suitable severity, DEBUG for processing stages. Apply
redaction at every level, clear request context on exit and keep telemetry bounded so a
slow or failed sink cannot decide the business outcome.

## Requirements and verification discipline

Write requirements from user outcomes and invariants before selecting mechanisms or tests.
Label architecture choices and their rationale explicitly. Test concurrency counts, fixture
sizes, pool values and timeouts do not become product requirements because code uses them.
When behavior changes, update affected existing scenarios and contracts together, preserving
unrelated guarantees; distinguish proposed, implemented, checked and independently accepted.

Verify the boundary where an invariant could fail: database effects and actual contention,
crash recovery and stale ownership, context isolation and redaction, configuration precedence,
and resource cleanup. Assertions should reject plausible incorrect implementations rather
than mirror helper structure. Use static checks for dependency direction where useful and
integration tests for runtime guarantees. A documentation/parser check is not a runtime PASS.
