# Approved foundation

## stack
Python 3.15; FastAPI and Pydantic v2; PostgreSQL 17; psycopg 3 with explicit SQL and pooling, no ORM. One service, one relational database. Firm UUID lookup uses a unique index. A third bulk_payment_requests table owns payer-scoped idempotency keys, normalized request fingerprints and durable HTTP results. Transactional row locks coordinate load-balanced instances; lock firms in internal-ID order. Docker service, Compose local PostgreSQL, Testcontainers verification.

## conventions
Operator accepted architecture and scope in chat: "approve the scope" after the additional idempotency table/header/409/replayed-422 proposal. This resolves design intent only, not installation authority. Small explicit modules and SQL; exact cents, no floating-point money. Atomic payment/balance/idempotency commit; sufficient-funds check after locks; duplicate recipients supported. Payer and payee cannot match. Malformed input, empty batches and unknown firms return 422; successful batches return 201; changed payload for an existing payer/key returns 409. Normalize UUIDs and monetary values before fingerprinting; preserve payment order and descriptions. Do not acquire new resources outside deterministic lock ordering. Public API/schema changes require operator decision.

## tooling
uv 0.13.1 and committed uv.lock; Python 3.15; Ruff format/lint; strict mypy; pytest and pytest-bdd; GitHub Actions checks. Foundation check covers lock consistency, formatting, lint and typing. Payment tests and CI test invocation follow implementation. Keep dependencies proportional. Setup explicitly selects uv-managed Python 3.15 because the host Homebrew pyexpat extension fails to load. Isolated Python 3.15 dependency installation, lock consistency, Ruff, mypy and ASGI shell smoke check passed before requesting approval.

## deployment
Containerized non-root service on port 8000; Docker Compose PostgreSQL 17 with named data volume and readiness healthcheck. Local ports bind to loopback. Local-only development credentials; no cloud deployment, publishing or real-money operation authorized. PostgreSQL is authoritative; no process-local locks or state for correctness.

## testing
Use real PostgreSQL via Testcontainers for executable Gherkin/API journeys and SQL/concurrency boundaries. Verify all-or-nothing persistence, exact-balance and insufficient-funds outcomes, concurrent competing spending, repeated recipients, overflow, invalid requests, unknown firms, self-payment rejection, key conflicts, simultaneous retries, replay including 422, and rollback. Selective unit tests for parsing/fingerprinting. Foundation scaffold alone implements no payment behavior and claims no payment verification.

## autonomy
Foundation approval authorizes minimal scaffold installation only; harness installs guidance/provenance, commits approved files and initializes Beads. Then ordinary project work follows the accepted outcome with coordinator-owned task tracking and no new formal campaign unless requested. Single coordinator; no delegated agents planned. Bounded commands, no unrelated artifacts or publication; reserve at least 20 percent of implementation effort for verification and handoff. Routine retries and checks remain agent-managed, with original evidence retained. Scope/interface/authority changes return to operator.

## invariants
Money remains integer cents with overflow checks; successful transfers conserve total funds and never leave any negative balance; a bulk request either commits all payment/balance effects or none. Payer-scoped idempotency executes a committed request at most once and replays its durable response, including insufficient-funds 422; mismatched reuse returns 409. A rolled-back database failure does not retain a completed idempotency outcome. Indexed unique firm UUIDs. Preserve existing Git metadata and unrelated files; .gitignore replacement preserves all existing bytes and appends tooling entries. No real-payment claims, fabricated approval, approval-store deletion or feature implementation before foundation installation.

## recovery_cache_paths
[]

## service_tier
priority

## scaffold
{
  "commands": {
    "setup": [
      "uv",
      "sync",
      "--locked",
      "--all-groups",
      "--managed-python"
    ],
    "start": [
      "uv",
      "run",
      "--locked",
      "uvicorn",
      "firm_payments.app:app",
      "--host",
      "0.0.0.0",
      "--port",
      "8000"
    ],
    "check": [
      "uv",
      "run",
      "--locked",
      "python",
      "scripts/check.py"
    ]
  },
  "file_sha256": {
    "pyproject.toml": "d54f06931706147dea0f86f1f6dd0ce45d86a5634e502681d8bcd27b2ccd29d0",
    "uv.lock": "d81b2d38b52c57162311024b7135fd4065b41113acb730292344d90fe1b0cf86",
    ".python-version": "1253c91eccb2aac1c6ff2ac03fd69c1aaa0b221abd3d1bf5b563c12602452b4e",
    "firm_payments/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "firm_payments/app.py": "565afe5a8b44c51c5cba4a9ac5d1e16ae2bbb797f08763527584b8c0d0af88ac",
    "scripts/check.py": "4e8d049b23df7c142e05258325fa43c24aa763034ac05202a2f0b55caa4756e8",
    "Dockerfile": "8a599b9b8f8dcab7cfb32ed3ebc18f73a81a864f4e71ae5c846fb8190909ef90",
    ".dockerignore": "b28016fccb056fde88b90ab406800370f7579ee4eff5980a967a802e86cef1b3",
    "compose.yaml": "dd0300fa17295ca15a55515226a65630fb7af947850b83a4c272b6a43eeaf7ba",
    ".github/workflows/ci.yaml": "a5b968527cc04b4b1c1ab3b8fdeecc487af8bcfd2e65a464f8c20f88e81b88aa",
    "README.md": "2e59e1d49c35058095b6fa1ff25323b289f19cd3843e8e4d22f0d756cb62e007",
    ".gitignore": "ee80e4fe14acf7e06fe4cd8e60d15d752fc64f6a231d5c9ae1eb5e581688e23b",
    "docs/FOUNDATION_DESIGN.md": "1a6e4cb904a004f0b0d126b0d0c84ca8e79e7d1fbe4dfec08bafaeaf5c2b26da"
  }
}
