"""Project-local trusted-host authority and bounded, candidate-bound check evidence."""

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
STORE = Path("/Users/kirillf/devel/codex-harness/.approvals")
HELPER = Path("/Users/kirillf/devel/codex-harness/working/current-e3663724ccc4/approval_tools.py")
HELPER_SHA = "3ec07e8bd5bb16ee7a5468b342947537855855ad0172303d7c0511d979122a82"
POLICY = "a4d47cdc255ebc2e2081beb0b8badf0fcf3c1a3df06a369e788ee29f76024370"
ASYNC_AMENDMENT = "13d0073d08da901b8b9024cc10b6a99eeafcc70bd034246bce4b438a3f78d545"
DESCRIPTION_AMENDMENT = "f84b45053cd02e216eaef9337c8f635008f6b379f8d3c87ba0f9da16255f74b2"
STATE = ROOT / ".harness/project-local-workflow"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


def approval(payload: Path, expected: dict[str, Any], store: Path = STORE) -> dict[str, Any]:
    """Reuse the pinned shared verifier; never synthesize approval evidence."""
    if digest(HELPER.read_bytes()) != HELPER_SHA:
        raise ValueError("Shared approval verifier changed; review required")
    spec = importlib.util.spec_from_file_location("project_shared_approvals", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    value = cast(dict[str, Any], module.verify(payload, store, expected))
    if value.get("approval") != {"mode": "interactive-local-v1", "directory": str(store)}:
        raise ValueError("Approval store binding mismatch")
    return value


def policy() -> dict[str, Any]:
    value = approval(
        STORE / f"{POLICY}.payload.json",
        {
            "purpose": "project-local-execution-policy-amendment",
            "project": str(ROOT),
            "scope_id": "bulk-payments",
        },
    )
    for name, expected in value["artifacts"].items():
        if digest(Path(name).read_bytes()) != expected:
            raise ValueError("Approved policy artifact changed")
    return value


def manifest(root: Path = ROOT) -> dict[str, str]:
    names = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
        timeout=10,
    ).stdout.split(b"\0")
    result = {}
    for raw in sorted(set(names) - {b""}):
        name = os.fsdecode(raw)
        path = root / name
        if path.is_symlink() or root.resolve() not in path.resolve().parents:
            raise ValueError("Candidate contains symlink or escaping path")
        if path.is_file():
            result[name] = digest(path.read_bytes()) + ":" + oct(path.stat().st_mode & 0o777)
        elif path.exists():
            raise ValueError("Candidate contains nonregular file")
        else:
            result[name] = "DELETED"
    return result


def redact(data: bytes, secrets: tuple[str, ...]) -> bytes:
    for secret in sorted(secrets, key=len, reverse=True):
        if secret:
            data = data.replace(secret.encode(), b"[REDACTED]")
    return data


def run_command(
    argv: list[str],
    directory: Path,
    *,
    timeout: float = 300,
    secrets: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
    max_output: int = 1_048_576,
    deadline: float | None = None,
) -> dict[str, Any]:
    """Bound aggregate output on disk; timeout/overflow can never establish PASS."""
    if not 0 < timeout <= 600 or max_output < 1:
        raise ValueError("Invalid command bounds")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    started = time.monotonic()
    # The absolute phase end includes native termination, not a fresh cleanup window.
    phase_end = deadline if deadline is not None else started + timeout + 7
    timeout = min(timeout, phase_end - started - 7)
    if timeout <= 0:
        raise ValueError("Insufficient aggregate allowance for command cleanup")
    status = "FAIL"
    reason = "nonzero_exit"
    with tempfile.TemporaryFile() as raw:
        process = subprocess.Popen(
            argv,
            cwd=ROOT,
            env=env,
            stdout=raw,
            stderr=raw,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                if os.fstat(raw.fileno()).st_size > max_output:
                    reason = "output_limit"
                    break
                if time.monotonic() - started > timeout:
                    reason = "timeout"
                    break
                time.sleep(0.05)
            else:
                if process.returncode == 0 and os.fstat(raw.fileno()).st_size <= max_output:
                    status, reason = "PASS", "completed"
        finally:
            # Descendants share this new process group; stop them even if parent exited.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=min(2, max(0, phase_end - time.monotonic())))
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=min(5, max(0, phase_end - time.monotonic())))
            except subprocess.TimeoutExpired:
                status, reason = "FAIL", "process_cleanup_uncertain"
        if time.monotonic() >= phase_end:
            status, reason = "FAIL", "aggregate_deadline"
        if os.fstat(raw.fileno()).st_size > max_output:
            status, reason = "FAIL", "output_limit"
        elif status == "PASS" and time.monotonic() - started > timeout:
            status, reason = "FAIL", "timeout"
        raw.seek(0)
        log = redact(raw.read(max_output), secrets)
    log_path = directory / "command.log"
    log_path.write_bytes(log)
    result = {
        "origin": "project-local-trusted-host-v1",
        "argv": [redact(arg.encode(), secrets).decode() for arg in argv],
        "argv_sha256": digest(json.dumps(argv).encode()),
        "timeout_seconds": timeout,
        "status": status,
        "reason": reason,
        "exit_code": process.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "log": str(log_path),
        "log_sha256": digest(log),
    }
    save(directory / "command.json", result)
    return result


def environment() -> dict[str, Any]:
    return {
        "python": sys.version,
        "platform": sys.platform,
        "interpreter_sha256": digest(Path(sys.executable).resolve().read_bytes()),
        "lock_sha256": digest((ROOT / "uv.lock").read_bytes()),
        "installed": sorted(
            [d.metadata["Name"], d.version] for d in importlib.metadata.distributions()
        ),
    }


def check(group: str) -> Path:
    policy()
    commands = {
        "baseline-syntax": [sys.executable, "scripts/harness_checks.py", "baseline-syntax"],
        "quality": [sys.executable, "scripts/harness_checks.py", "quality"],
        "container-boot": [sys.executable, "scripts/harness_checks.py", "container-boot"],
        "money-unit": [sys.executable, "scripts/harness_checks.py", "money-unit"],
        "atomic-api": [sys.executable, "scripts/harness_checks.py", "atomic-api"],
        "uuid-index": [sys.executable, "scripts/harness_checks.py", "uuid-index"],
        "idempotency": [sys.executable, "scripts/harness_checks.py", "idempotency"],
        "contention": [sys.executable, "scripts/harness_checks.py", "contention"],
        "resource-failures": [sys.executable, "scripts/harness_checks.py", "resource-failures"],
        "observability": [sys.executable, "scripts/harness_checks.py", "observability"],
        "container-http": [sys.executable, "scripts/harness_checks.py", "container-http"],
        "local-policy": [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_project_workflow.py",
        ],
        "qualify-local": [sys.executable, "scripts/qualify_local.py", "exercise"],
        "qualify-local-negative": [sys.executable, "scripts/qualify_local.py", "negative"],
    }
    deadline: float | None = None
    if group not in commands:
        raise ValueError("Check group is not implemented; no placeholder PASS")
    if group in {
        "baseline-syntax",
        "local-policy",
        "quality",
        "container-boot",
        "money-unit",
        "atomic-api",
        "uuid-index",
        "idempotency",
        "contention",
        "resource-failures",
        "observability",
        "container-http",
    }:
        state = json.loads((STATE / "feature-state.json").read_text())
        authority = feature_authority(Path(state["payload"]))
        task = state["current_task"]
        if state.get("status") in {
            "LIFECYCLE_IMPLEMENTING",
            "CRUD_IMPLEMENTING",
            "REVIEW6_REPAIRING",
        }:
            if state.get("lifecycle_approval_sha256") != LIFECYCLE_AMENDMENT:
                raise ValueError("Unknown lifecycle authority")
            contract = json.loads((ROOT / "docs/features/bulk-payments/feature.json").read_text())
            tasks = {entry["id"]: entry for entry in contract["task_definitions"]}
            if task not in tasks:
                raise ValueError("Unknown lifecycle task")
            authority = dict(authority, tasks=tasks)
            definitions = {entry["id"]: entry for entry in contract["check_definitions"]}
            commands[group] = list(definitions[group]["argv"])
            if commands[group][0] == "python3":
                commands[group][0] = sys.executable
        if state.get("status") == "ALEMBIC_IMPLEMENTING":
            if state.get("aligned_alembic_approval_sha256") != ALIGNED_ALEMBIC_AMENDMENT:
                raise ValueError("Unknown aligned Alembic attempt")
            contract = json.loads((ROOT / "docs/features/bulk-payments/feature.json").read_text())
            tasks = {entry["id"]: entry for entry in contract["task_definitions"]}
            if (
                task
                not in {
                    "alembic-container-runtime",
                    "alembic-safe-adoption",
                    "alembic-startup-integration",
                }
                or task not in tasks
            ):
                raise ValueError("Unknown Alembic task")
            authority = dict(authority, tasks=tasks)
        if group not in authority["tasks"][task]["checks"]:
            raise ValueError("Check is not part of the current approved slice")
        deadline = (
            crud_deadline(state)
            if state.get("status") == "CRUD_IMPLEMENTING"
            else review6_repair_deadline(state)
            if state.get("status") == "REVIEW6_REPAIRING"
            else state["attempts"][task]["deadline_epoch"]
        )
        if state.get("status") in {
            "LIFECYCLE_IMPLEMENTING",
            "CRUD_IMPLEMENTING",
            "REVIEW6_REPAIRING",
        }:
            deadline = (
                crud_deadline(state)
                if state.get("status") == "CRUD_IMPLEMENTING"
                else review6_repair_deadline(state)
                if state.get("status") == "REVIEW6_REPAIRING"
                else lifecycle_deadline(state, task)
            )
        if state.get("status") == "VERSIONED_IMPLEMENTING":
            attempt = state["versioned_attempt"]
            if attempt["approval_sha256"] != VERSIONED_AMENDMENT:
                raise ValueError("Unknown versioned amendment attempt")
            repair = state["repairs"][-1]
            if state["repair_rounds_used"] != 4 or repair["round"] != 4:
                raise ValueError("Invalid supplemental repair counter")
            if repair["approval_sha256"] != VERSIONED_AMENDMENT:
                raise ValueError("Unapproved supplemental repair")
            review = Path(repair["review"])
            if digest(review.read_bytes()) != repair["review_sha256"]:
                raise ValueError("Supplemental repair review changed")
            deadline = min(attempt["deadline_epoch"], repair["deadline_epoch"])
        if state.get("status") == "ASYNC_IMPLEMENTING":
            attempt = state["async_attempt"]
            if attempt["approval_sha256"] != ASYNC_AMENDMENT:
                raise ValueError("Unknown async amendment attempt")
            deadline = attempt["deadline_epoch"]
        if state.get("status") == "AMENDMENT_IMPLEMENTING":
            amendment = state["amendment_attempt"]
            if amendment["approval_sha256"] != DESCRIPTION_AMENDMENT:
                raise ValueError("Unknown amendment attempt")
            deadline = amendment["deadline_epoch"]
        if state.get("status") == "REPAIRING":
            # A review repair is a separate bounded round, never a reset of the
            # completed implementation attempt or its historical evidence.
            repair = state["repairs"][-1]
            if not 1 <= repair["round"] == state["repair_rounds_used"] <= 3:
                raise ValueError("Invalid repair counter")
            review = Path(repair["review"])
            if digest(review.read_bytes()) != repair["review_sha256"]:
                raise ValueError("Repair review changed")
            if json.loads(review.read_text())["outcome"] != "CHANGES_REQUIRED":
                raise ValueError("Repair requires actionable review")
            deadline = repair["deadline_epoch"]
        if time.time() >= deadline:
            raise ValueError("Slice attempt deadline exhausted")
    aggregate_end = (
        time.monotonic() + max(0, deadline - time.time()) if deadline is not None else None
    )
    handoff_reserve = 30
    resource_cleanup = group in {
        "container-boot",
        "container-http",
        "observability",
        "atomic-api",
        "contention",
        "idempotency",
        "resource-failures",
    }
    # Resource cleanup has 30s work plus 7s native termination; handoff is protected.
    command_end = (
        aggregate_end - handoff_reserve - (37 if resource_cleanup else 0)
        if aggregate_end is not None
        else None
    )
    cleanup_end = aggregate_end - handoff_reserve if aggregate_end is not None else None
    before = manifest()
    environment_before = environment()
    run = STATE / "checks" / uuid.uuid4().hex
    child_environment = dict(os.environ, QUAL1_RUN_DIR=str(run), PAYMENTS_CHECK_RUN_DIR=str(run))
    result = run_command(
        commands[group],
        run,
        env=child_environment,
        timeout=30 if group == "baseline-syntax" else 300,
        deadline=command_end,
    )
    if group in {"qualify-local", "qualify-local-negative"}:
        for journal in run.rglob("ownership.json"):
            cleanup_result = run_command(
                [sys.executable, "scripts/qualify_local.py", "cleanup", str(journal.parent)],
                run / ("cleanup-" + journal.parent.name),
                timeout=30,
                deadline=cleanup_end,
            )
            if cleanup_result["status"] != "PASS":
                result.update(status="FAIL", reason="cleanup_uncertain")
    if group in {
        "container-boot",
        "container-http",
        "observability",
        "atomic-api",
        "contention",
        "idempotency",
        "resource-failures",
    }:
        cleanup_result = run_command(
            [sys.executable, "scripts/harness_checks.py", "cleanup", str(run)],
            run / "cleanup",
            timeout=30,
            deadline=cleanup_end,
        )
        if cleanup_result["status"] != "PASS":
            result.update(status="FAIL", reason="cleanup_uncertain")
    after = manifest()
    if environment_before != environment():
        result.update(status="FAIL", reason="environment_drift")
    if before != after:
        result.update(status="FAIL", reason="candidate_drift")
    completed_epoch = time.time()
    if aggregate_end is not None and (
        time.monotonic() >= aggregate_end - handoff_reserve
        or (deadline is not None and completed_epoch >= deadline - handoff_reserve)
    ):
        result.update(status="FAIL", reason="aggregate_deadline")
    result.update(aggregate_deadline_epoch=deadline, check_completed_epoch=completed_epoch)
    save(
        run / "evidence.json",
        {
            **result,
            "group": group,
            "candidate": before,
            "candidate_sha256": digest(json.dumps(before, sort_keys=True).encode()),
            "python": sys.version,
            "environment": environment_before,
            "artifacts": {
                str(path): digest(path.read_bytes())
                for path in run.glob("containers/*/runtime.json")
            },
            "policy_sha256": POLICY,
        },
    )
    # Evidence serialization itself cannot turn an expired check into accepted PASS.
    if deadline is not None and time.time() >= deadline:
        result.update(status="FAIL", reason="aggregate_deadline")
        saved = json.loads((run / "evidence.json").read_text())
        saved.update(status="FAIL", reason="aggregate_deadline")
        save(run / "evidence.json", saved)
    print(f"{group}: {result['status']}; evidence {run / 'evidence.json'}")
    if result["status"] != "PASS":
        raise RuntimeError("Project-local check failed; evidence retained")
    return run / "evidence.json"


def current_evidence(path: Path, group: str) -> dict[str, Any]:
    value = cast(dict[str, Any], json.loads(path.read_text()))
    if (
        value.get("origin") != "project-local-trusted-host-v1"
        or value.get("policy_sha256") != POLICY
        or value.get("group") != group
        or value.get("status") != "PASS"
        or value.get("candidate") != manifest()
        or value.get("environment") != environment()
        or digest(Path(value["log"]).read_bytes()) != value.get("log_sha256")
    ):
        raise ValueError("Missing, failed, stale or altered project-local evidence")
    if value.get("aggregate_deadline_epoch") is not None and (
        value.get("check_completed_epoch", float("inf")) >= value["aggregate_deadline_epoch"]
    ):
        raise ValueError("Check completed after aggregate deadline")
    for artifact, expected in value.get("artifacts", {}).items():
        if digest(Path(artifact).read_bytes()) != expected:
            raise ValueError("Altered check runtime artifact")
    return value


def description_design_overrides(feature: dict[str, Any], feature_sha: str) -> dict[str, str]:
    """Only this exact audited amendment can replace its four original design hashes."""
    amended = approval(
        STORE / f"{DESCRIPTION_AMENDMENT}.payload.json",
        {
            "purpose": "project-local-contract-amendment-v1",
            "project": str(ROOT),
            "scope_id": "bulk-payments",
            "feature_request_sha256": feature_sha,
            "policy_request_sha256": POLICY,
            "gate": "description-sanitization-implementation",
        },
    )
    directory = ROOT / ".harness/project-local-workflow/description-sanitization-amendment"
    index_path = directory / "artifacts.json"
    if amended["artifacts"] != {str(index_path): digest(index_path.read_bytes())}:
        raise ValueError("Amendment artifact index changed")
    index = json.loads(index_path.read_text())
    for name, expected in index.items():
        path = Path(name)
        if path.is_symlink() or ROOT not in path.resolve().parents:
            raise ValueError("Amendment artifact escapes project")
        if digest(path.read_bytes()) != expected:
            raise ValueError("Approved amendment artifact changed")
    changes_path = directory / "design-changes.json"
    if str(changes_path) not in index:
        raise ValueError("Missing amendment design bindings")
    changes = json.loads(changes_path.read_text())
    if set(changes) != {
        "docs/features/bulk-payments/" + name
        for name in ("requirements.md", "api.md", "code-design.md", "scenarios.feature")
    }:
        raise ValueError("Unexpected amendment design paths")
    result = {}
    for name, binding in changes.items():
        if binding["preimage_sha256"] != feature["artifacts"].get(name):
            raise ValueError("Amendment does not extend original design")
        if index.get(binding["proposed_path"]) != binding["postimage_sha256"]:
            raise ValueError("Unbound amendment postimage")
        result[name] = binding["postimage_sha256"]
    return result


def async_design_overrides(
    feature: dict[str, Any], feature_sha: str, prior: dict[str, str]
) -> dict[str, str]:
    amended = approval(
        STORE / f"{ASYNC_AMENDMENT}.payload.json",
        {
            "purpose": "project-local-async-payments-amendment-v1",
            "project": str(ROOT),
            "scope_id": "bulk-payments",
            "feature_request_sha256": feature_sha,
            "sanitization_amendment_sha256": DESCRIPTION_AMENDMENT,
            "policy_request_sha256": POLICY,
            "gate": "async-payment-lifecycle-implementation",
        },
    )
    directory = ROOT / ".harness/project-local-workflow/async-payments-amendment"
    index_path = directory / "artifacts.json"
    if amended["artifacts"] != {str(index_path): digest(index_path.read_bytes())}:
        raise ValueError("Async amendment index changed")
    index = json.loads(index_path.read_text())
    for name, expected in index.items():
        path = Path(name)
        if (
            path.is_symlink()
            or ROOT not in path.resolve().parents
            or digest(path.read_bytes()) != expected
        ):
            raise ValueError("Async amendment artifact changed or escaped")
    changes_path = directory / "design-changes.json"
    if str(changes_path) not in index:
        raise ValueError("Async design bindings missing")
    changes = json.loads(changes_path.read_text())
    if set(changes) != {
        "docs/features/bulk-payments/" + name
        for name in ("architecture.md", "api.md", "code-design.md", "scenarios.feature")
    }:
        raise ValueError("Unexpected async design paths")
    result = dict(prior)
    for name, binding in changes.items():
        if binding["preimage_sha256"] != prior.get(name, feature["artifacts"].get(name)):
            raise ValueError("Async amendment does not extend current chain")
        if index.get(binding["proposed_path"]) != binding["postimage_sha256"]:
            raise ValueError("Unbound async postimage")
        result[name] = binding["postimage_sha256"]
    return result


VERSIONED_AMENDMENT = "11b6dc30428e88320fa0edc52fd2e93f6dec6934840f15e0d5d5321054c58574"


def versioned_design_overrides(feature: dict[str, Any], prior: dict[str, str]) -> dict[str, str]:
    amended = approval(
        STORE / f"{VERSIONED_AMENDMENT}.payload.json",
        {
            "purpose": "project-local-versioned-app-amendment-v1",
            "project": str(ROOT),
            "scope_id": "bulk-payments",
            "gate": "versioned-app-service-implementation",
            "parent_async_approval_sha256": ASYNC_AMENDMENT,
        },
    )
    for name, expected in amended["artifacts"].items():
        path = Path(name)
        if path.is_symlink() or ROOT not in path.resolve().parents:
            raise ValueError("Versioned artifact escapes project")
        if digest(path.read_bytes()) != expected:
            raise ValueError("Versioned artifact changed")
    index_path = STATE / "versioned-app-amendment/design-postimages.json"
    if str(index_path) not in amended["artifacts"]:
        raise ValueError("Missing versioned design bindings")
    changes = json.loads(index_path.read_text())
    if set(changes) != {
        "docs/features/bulk-payments/" + name
        for name in (
            "requirements.md",
            "architecture.md",
            "api.md",
            "code-design.md",
            "scenarios.feature",
            "slices.md",
        )
    }:
        raise ValueError("Unexpected versioned design paths")
    result = dict(prior)
    for name, binding in changes.items():
        if binding["preimage_sha256"] != prior.get(name, feature["artifacts"].get(name)):
            raise ValueError("Versioned amendment does not extend approved chain")
        if amended["artifacts"].get(binding["postimage"]) != binding["postimage_sha256"]:
            raise ValueError("Unbound versioned postimage")
        result[name] = binding["postimage_sha256"]
    return result


ALIGNED_ALEMBIC_AMENDMENT = "a9a6e982a7127b7249c7224734cb2b5f085ec076a55a2a8d353c55e5d3118e77"


def aligned_alembic_overrides(feature: dict[str, Any], prior: dict[str, str]) -> dict[str, str]:
    amended = approval(
        STORE / f"{ALIGNED_ALEMBIC_AMENDMENT}.payload.json",
        {
            "purpose": "project-local-alembic-alignment-amendment-v1",
            "project": str(ROOT),
            "scope_id": "bulk-payments",
            "gate": "aligned-alembic-contract-and-implementation",
            "parent_versioned_approval_sha256": VERSIONED_AMENDMENT,
        },
    )
    for name, expected in amended["artifacts"].items():
        path = Path(name)
        if path.is_symlink() or ROOT not in path.resolve().parents:
            raise ValueError("Aligned Alembic artifact escapes project")
        if digest(path.read_bytes()) != expected:
            raise ValueError("Aligned Alembic artifact changed")
    index_path = STATE / "alembic-aligned-amendment/design-postimages.json"
    if str(index_path) not in amended["artifacts"]:
        raise ValueError("Missing aligned Alembic design bindings")
    changes = json.loads(index_path.read_text())
    if set(changes) != {
        "docs/features/bulk-payments/" + name
        for name in (
            "requirements.md",
            "architecture.md",
            "api.md",
            "code-design.md",
            "scenarios.feature",
            "slices.md",
            "feature.json",
        )
    }:
        raise ValueError("Unexpected aligned Alembic design paths")
    result = dict(prior)
    for name, binding in changes.items():
        if binding["preimage_sha256"] != prior.get(name, feature["artifacts"].get(name)):
            raise ValueError("Aligned Alembic amendment does not extend approved chain")
        if amended["artifacts"].get(binding["postimage"]) != binding["postimage_sha256"]:
            raise ValueError("Unbound aligned Alembic postimage")
        result[name] = binding["postimage_sha256"]
    return result


LIFECYCLE_AMENDMENT = "beb372ab543562b93fe9bffa3d7a951f5f5ffafba5a992240d55a44557cf6053"
STANDARDS_AMENDMENT = "12bf57961f7363d83f3e45ec5e3698cbc9ea6587fce3757cac5fd45454122bc9"
MIGRATION_CONTINUATION = "c666bedd1503b152a08b51737320c2c0c9a41ee26ec48605f41d25f1aaaa8e53"


def lifecycle_deadline(state: dict[str, Any], task: str) -> float:
    attempt = state["attempts"][task]
    continuations = attempt.get("continuations", [])
    if not continuations:
        # Infrastructure retries never replenish an aggregate slice allowance.
        history = state.get("attempt_history", {}).get(task, [])
        return min(float(item["deadline_epoch"]) for item in [*history, attempt])
    if task != "lifecycle-container-migration" or len(continuations) != 1:
        raise ValueError("Unapproved lifecycle continuation")
    extension = continuations[0]
    if extension["approval_sha256"] != MIGRATION_CONTINUATION:
        raise ValueError("Unknown continuation authority")
    value = approval(
        STORE / f"{MIGRATION_CONTINUATION}.payload.json",
        {
            "purpose": "project-local-lifecycle-migration-continuation-v1",
            "project": str(ROOT),
            "task": task,
            "parent_implementation_approval_sha256": LIFECYCLE_AMENDMENT,
        },
    )
    for name, expected in value["artifacts"].items():
        if digest(Path(name).read_bytes()) != expected:
            # The completed continuation bound the then-live feature contract.
            # Preserve that exact historical snapshot while authenticating its
            # explicitly approved successor; never rewrite the old approval.
            frozen = STATE / "correlation-settings-aligned-amendment/postimages/feature.json"
            if (
                name != str(ROOT / "docs/features/bulk-payments/feature.json")
                or digest(frozen.read_bytes()) != expected
            ):
                raise ValueError("Continuation artifact changed")
            feature_authority(Path(state["payload"]))
    if (
        attempt["deadline_epoch"] != value["original_deadline_epoch"]
        or attempt["started_epoch"] != value["original_started_epoch"]
        or extension["started_epoch"] < attempt["deadline_epoch"]
        or extension["deadline_epoch"] - extension["started_epoch"] != value["additional_seconds"]
    ):
        raise ValueError("Continuation changed original history or allowance")
    return float(extension["deadline_epoch"])


def lifecycle_design_overrides(feature: dict[str, Any], prior: dict[str, str]) -> dict[str, str]:
    """Authenticate new postimages and the exact one-file standards baseline advance."""
    amended = approval(
        STORE / f"{LIFECYCLE_AMENDMENT}.payload.json",
        {
            "purpose": "project-local-correlation-lifecycle-implementation-v1",
            "project": str(ROOT),
            "scope_id": "bulk-payments",
            "gate": "aligned-correlation-lifecycle-contract-and-implementation",
            "parent_alembic_approval_sha256": ALIGNED_ALEMBIC_AMENDMENT,
        },
    )
    standards = approval(
        STORE / f"{STANDARDS_AMENDMENT}.payload.json",
        {
            "purpose": "project-local-standards-baseline-amendment-v1",
            "project": str(ROOT),
            "scope_id": "bulk-payments",
            "parent_implementation_approval_sha256": LIFECYCLE_AMENDMENT,
        },
    )
    design = approval(
        STORE / f"{amended['parent_design_approval_sha256']}.payload.json",
        {
            "purpose": "project-local-correlation-idempotency-design-acceptance-v1",
            "project": str(ROOT),
            "scope_id": "bulk-payments",
        },
    )
    for authority in (amended, standards, design):
        for name, expected in authority["artifacts"].items():
            path = Path(name)
            if path.is_symlink() or ROOT not in path.resolve().parents:
                raise ValueError("Lifecycle artifact escapes project")
            if digest(path.read_bytes()) != expected:
                raise ValueError("Approved lifecycle artifact changed")
    directory = STATE / "correlation-settings-aligned-amendment"
    changes_path = directory / "design-postimages.json"
    if str(changes_path) not in amended["artifacts"]:
        raise ValueError("Unbound lifecycle index")
    changes = json.loads(changes_path.read_text())
    if set(changes) != {str(ROOT / name) for name in prior if name.startswith("docs/")}:
        raise ValueError("Unexpected lifecycle postimage paths")
    result = dict(prior)
    for name, binding in changes.items():
        relative = str(Path(name).relative_to(ROOT))
        if binding["preimage_sha256"] != prior.get(relative, feature["artifacts"].get(relative)):
            raise ValueError("Lifecycle amendment does not extend approved chain")
        if amended["artifacts"].get(binding["postimage"]) != binding["postimage_sha256"]:
            raise ValueError("Unbound lifecycle postimage")
        result[relative] = binding["postimage_sha256"]
    change = standards["change"]
    if change["path"] != "CODING_STANDARDS.md" or change["preimage_sha256"] != feature[
        "artifacts"
    ].get("CODING_STANDARDS.md"):
        raise ValueError("Standards amendment does not extend original authority")
    old = json.loads((directory / "source-baseline.json").read_text())["source_manifest"]
    new = json.loads((STATE / "standards-baseline-amendment/source-baseline.json").read_text())[
        "source_manifest"
    ]
    if {name for name in old.keys() | new.keys() if old.get(name) != new.get(name)} != {
        "CODING_STANDARDS.md"
    } or new["CODING_STANDARDS.md"] != change["postimage_sha256"] + ":" + change["mode"]:
        raise ValueError("Standards baseline contains unauthorized drift")
    result["CODING_STANDARDS.md"] = change["postimage_sha256"]
    return result


SINGLE_POOL_AMENDMENT = "18c6b7c7b4ec54af9229a648f6076b5f9976a874ae2d8f73155b331b2ab00d2a"


def single_pool_design_overrides(prior: dict[str, str]) -> dict[str, str]:
    """Authenticate approved semantic alignment without rewriting historical payloads."""
    amended = approval(
        STORE / f"{SINGLE_POOL_AMENDMENT}.payload.json",
        {
            "purpose": "project-local-single-pool-alembic-contract-amendment-v1",
            "project": str(ROOT),
            "scope_id": "bulk-payments",
            "parent_implementation_approval_sha256": LIFECYCLE_AMENDMENT,
        },
    )
    for name, expected in amended["artifacts"].items():
        path = Path(name)
        if path.is_symlink() or ROOT not in path.resolve().parents:
            raise ValueError("Simplification artifact escapes project")
        if digest(path.read_bytes()) != expected:
            raise ValueError("Approved simplification artifact changed")
    directory = STATE / "single-pool-alembic-amendment"
    baseline = json.loads((directory / "source-baseline.json").read_text())["files"]
    alignment = json.loads((directory / "contract-alignment.json").read_text())
    if alignment["approval_sha256"] != SINGLE_POOL_AMENDMENT:
        raise ValueError("Unknown contract alignment authority")
    changes = alignment["changes"]
    expected_paths = {name for name in prior if name.startswith("docs/features/")}
    if set(changes) != expected_paths:
        raise ValueError("Unexpected simplification contract paths")
    result = dict(prior)
    for name, change in changes.items():
        if change["preimage_sha256"] != prior[name] or baseline.get(name) != prior[name]:
            raise ValueError("Simplification does not extend approved preimages")
        snapshot = directory / "aligned-contracts" / Path(name).name
        if snapshot.is_symlink() or digest(snapshot.read_bytes()) != change["postimage_sha256"]:
            raise ValueError("Aligned contract snapshot changed")
        result[name] = change["postimage_sha256"]
    return result


CRUD_AMENDMENT = "b5277513d12e616365d018a2c6c9bdc47885315050750cdda211dc1aa4def074"


def crud_authority() -> dict[str, Any]:
    value = approval(
        STORE / f"{CRUD_AMENDMENT}.payload.json",
        {
            "purpose": "project-local-crud-boundaries-and-authority-revalidation-v1",
            "project": str(ROOT),
            "scope_id": "bulk-payments",
        },
    )
    for name, expected in value["artifacts"].items():
        path = Path(name)
        if path.is_symlink() or ROOT not in path.resolve().parents:
            raise ValueError("CRUD authority artifact escapes project")
        if digest(path.read_bytes()) != expected:
            raise ValueError("Approved CRUD authority artifact changed")
    return value


def crud_design_overrides(prior: dict[str, str]) -> dict[str, str]:
    directory = STATE / "crud-authority-amendment"
    if not (directory / "contract-alignment.json").exists():
        return prior
    crud_authority()
    alignment = json.loads((directory / "contract-alignment.json").read_text())
    if alignment["approval_sha256"] != CRUD_AMENDMENT:
        raise ValueError("Unknown CRUD contract alignment authority")
    changes = alignment["changes"]
    if set(changes) != {name for name in prior if name.startswith("docs/features/")}:
        raise ValueError("Unexpected CRUD contract paths")
    result = dict(prior)
    for name, change in changes.items():
        if change["preimage_sha256"] != prior[name]:
            raise ValueError("CRUD contract preimage mismatch")
        snapshot = directory / "aligned-contracts" / Path(name).name
        if snapshot.is_symlink() or digest(snapshot.read_bytes()) != change["postimage_sha256"]:
            raise ValueError("CRUD contract snapshot changed")
        result[name] = change["postimage_sha256"]
    return result


def crud_deadline(state: dict[str, Any]) -> float:
    crud_authority()
    if state.get("crud_approval_sha256") != CRUD_AMENDMENT:
        raise ValueError("Unknown CRUD slice authority")
    attempt = state["crud_attempt"]
    if attempt["deadline_epoch"] != attempt["started_epoch"] + 1200:
        raise ValueError("CRUD aggregate allowance changed")
    return float(attempt["deadline_epoch"])


REVIEW6_REPAIR = "dd2346bcd4c318fccd6542ac0f7e8ecef6343cbae350256a47758c92deb2b29b"


def review6_repair_authority() -> dict[str, Any]:
    value = approval(
        STORE / f"{REVIEW6_REPAIR}.payload.json",
        {
            "purpose": "project-local-review6-finding-bound-repair-v1",
            "project": str(ROOT),
            "scope_id": "bulk-payments",
        },
    )
    for name, expected in value["artifacts"].items():
        path = Path(name)
        if path.is_symlink() or ROOT not in path.resolve().parents:
            raise ValueError("Repair artifact escapes project")
        if digest(path.read_bytes()) != expected:
            raise ValueError("Approved repair artifact changed")
    return value


def review6_repair_deadline(state: dict[str, Any]) -> float:
    review6_repair_authority()
    attempt = state["review6_repair_attempt"]
    if attempt["approval_sha256"] != REVIEW6_REPAIR or state["repair_rounds_used"] != 5:
        raise ValueError("Unknown finding-bound repair authority")
    if attempt["deadline_epoch"] != attempt["started_epoch"] + 1200:
        raise ValueError("Repair aggregate allowance changed")
    return float(attempt["deadline_epoch"])


def review6_design_overrides(prior: dict[str, str]) -> dict[str, str]:
    directory = STATE / "review6-repair-amendment"
    if not (directory / "contract-alignment.json").exists():
        return prior
    review6_repair_authority()
    alignment = json.loads((directory / "contract-alignment.json").read_text())
    if alignment["approval_sha256"] != REVIEW6_REPAIR:
        raise ValueError("Unknown review6 alignment authority")
    changes = alignment["changes"]
    if set(changes) != {name for name in prior if name.startswith("docs/features/")}:
        raise ValueError("Unexpected review6 contract paths")
    result = dict(prior)
    for name, change in changes.items():
        snapshot = directory / "aligned-contracts" / Path(name).name
        if change["preimage_sha256"] != prior[name]:
            raise ValueError("Repair contract preimage mismatch")
        if snapshot.is_symlink() or digest(snapshot.read_bytes()) != change["postimage_sha256"]:
            raise ValueError("Repair contract snapshot changed")
        result[name] = change["postimage_sha256"]
    return result


def feature_authority(payload: Path) -> dict[str, Any]:
    policy()
    value = approval(
        payload,
        {
            "purpose": "project-local-feature-v1",
            "project": str(ROOT),
            "scope_id": "bulk-payments",
            "policy_sha256": POLICY,
        },
    )
    overrides = description_design_overrides(value, digest(payload.read_bytes()))
    overrides = async_design_overrides(value, digest(payload.read_bytes()), overrides)
    overrides = versioned_design_overrides(value, overrides)
    overrides = aligned_alembic_overrides(value, overrides)
    overrides = lifecycle_design_overrides(value, overrides)
    overrides = single_pool_design_overrides(overrides)
    overrides = crud_design_overrides(overrides)
    overrides = review6_design_overrides(overrides)
    for name, original in value["artifacts"].items():
        expected = overrides.get(name, original)
        path = ROOT / name
        if path.is_symlink() or ROOT not in path.resolve().parents:
            raise ValueError("Feature artifact escapes project")
        if digest(path.read_bytes()) != expected:
            raise ValueError("Approved feature design changed")
    return value


def record_delivery(payload: Path, task: str, evidence: dict[str, Path]) -> Path:
    """Record a local delivery only after exact feature authority and dependency checks."""
    authority = feature_authority(payload)
    definition = authority["tasks"][task]
    for dependency in definition["depends_on"]:
        prior_path = STATE / "deliveries" / (dependency + ".json")
        prior = json.loads(prior_path.read_text())
        if prior.get("feature_sha256") != digest(payload.read_bytes()):
            raise ValueError("Dependency belongs to another feature approval")
        # Previously delivered owned files must still match the accepted local delivery.
        now = manifest()
        if any(
            now.get(name) != value and name not in definition["owned_paths"]
            for name, value in prior["owned_source"].items()
        ):
            raise ValueError("Dependency source changed; fresh prerequisite checks required")
    expected_source = dict(authority["source_manifest"])
    for delivered_task in authority["tasks"]:
        previous = STATE / "deliveries" / (delivered_task + ".json")
        if previous.exists():
            record = json.loads(previous.read_text())
            if record.get("feature_sha256") != digest(payload.read_bytes()):
                raise ValueError("Existing delivery belongs to another approval")
            expected_source.update(record["owned_source"])
    current = manifest()
    changed = {
        name
        for name in set(expected_source) | set(current)
        if expected_source.get(name) != current.get(name)
    }
    if changed - set(definition["owned_paths"]):
        raise ValueError("Unattributed changes outside approved slice ownership")
    required = set(definition["checks"])
    if set(evidence) != required or not required:
        raise ValueError("Incomplete or extra delivery check coverage")
    validated = {group: current_evidence(path, group) for group, path in evidence.items()}
    definitions = {item["id"]: item for item in authority["check_definitions"]}
    for group, result in validated.items():
        expected_argv = list(definitions[group]["argv"])
        if expected_argv[0] == "python3":
            expected_argv[0] = sys.executable
        if result["argv"] != expected_argv:
            raise ValueError("Check command differs from exact approved definition")
    candidate = manifest()
    owned = {name: value for name, value in candidate.items() if name in definition["owned_paths"]}
    if not owned:
        raise ValueError("Delivery has no owned source")
    path = STATE / "deliveries" / (task + ".json")
    if path.exists():
        raise ValueError("Delivery already recorded; explicit repair/amendment required")
    save(
        path,
        {
            "origin": "project-local-trusted-host-v1",
            "task": task,
            "feature_sha256": digest(payload.read_bytes()),
            "owned_source": owned,
            "checks": {
                group: {
                    "path": str(evidence[group]),
                    "sha256": digest(evidence[group].read_bytes()),
                }
                for group in validated
            },
            "candidate": candidate,
            "status": "DELIVERED_LOCAL",
        },
    )
    return path


def setup() -> None:
    policy()
    env = dict(
        os.environ,
        UV_PYTHON_INSTALL_DIR=str(ROOT / ".uv-python"),
        UV_CACHE_DIR=str(ROOT / ".uv-cache"),
        UV_PYTHON="3.15",
    )
    run = STATE / "setup" / uuid.uuid4().hex
    for name, argv in [
        ("install", ["uv", "python", "install", "3.15"]),
        ("sync", ["uv", "sync", "--locked", "--all-groups", "--managed-python"]),
    ]:
        result = run_command(argv, run / name, timeout=600, env=env)
        if result["status"] != "PASS":
            raise RuntimeError(f"Managed Python setup failed; evidence {run}")
    print(f"Managed Python setup PASS; evidence {run}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "policy", "setup"])
    parser.add_argument("group", nargs="?")
    args = parser.parse_args()
    if args.command == "policy":
        policy()
        print("Verified exact project-local policy approval")
    elif args.command == "setup":
        setup()
    elif args.group:
        check(args.group)
    else:
        parser.error("check requires a group")


if __name__ == "__main__":
    main()
