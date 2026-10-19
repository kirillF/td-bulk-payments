"""Negative authority and candidate-evidence tests; no real approval-store writes."""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from scripts import project_workflow as workflow


def test_actual_policy_is_authenticated():
    assert workflow.policy()["gate"] == "QUAL-1-project-local-override"


def test_missing_approval_fails_closed(tmp_path):
    store = tmp_path / "disposable-approvals"
    store.mkdir(mode=0o700)
    payload = tmp_path / "payload.json"
    payload.write_text("{}")
    with pytest.raises(ValueError, match="confirmation required"):
        workflow.approval(payload, {}, store)


def test_altered_approved_payload_fails(tmp_path):
    # Explicit test-only decision injection into the shared verifier's disposable store.
    spec = importlib.util.spec_from_file_location("approval_test_fixture", workflow.HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    store = tmp_path / "disposable-approvals"
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps(
            {
                "approval": {"mode": "interactive-local-v1", "directory": str(store)},
                "project": "fixture",
            }
        )
    )
    result = module.confirm(payload, store, decide=lambda: "APPROVE")
    snapshot = Path(result["payload"])
    assert workflow.approval(snapshot, {"project": "fixture"}, store)["project"] == "fixture"
    with pytest.raises(ValueError, match="binding mismatch"):
        workflow.approval(snapshot, {"project": "another-project"}, store)
    altered = tmp_path / "altered.json"
    altered.write_bytes(snapshot.read_bytes() + b" ")
    with pytest.raises(ValueError):
        workflow.approval(altered, {}, store)


def test_future_check_is_not_a_pass():
    with pytest.raises(ValueError, match="not implemented"):
        workflow.check("future-unimplemented")


def test_manifest_observes_untracked_deleted_and_symlink(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    source = tmp_path / "source.py"
    source.write_text("first")
    first = workflow.manifest(tmp_path)
    source.write_text("second")
    assert workflow.manifest(tmp_path) != first
    subprocess.run(["git", "add", "source.py"], cwd=tmp_path, check=True)
    source.unlink()
    assert workflow.manifest(tmp_path)["source.py"] == "DELETED"
    source.symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="symlink"):
        workflow.manifest(tmp_path)


def test_current_evidence_rejects_drift_or_log_tamper(tmp_path, monkeypatch):
    candidate = {"source.py": "hash:mode"}
    monkeypatch.setattr(workflow, "manifest", lambda: candidate)
    log = tmp_path / "check.log"
    log.write_bytes(b"pass")
    record = tmp_path / "evidence.json"
    value = {
        "origin": "project-local-trusted-host-v1",
        "policy_sha256": workflow.POLICY,
        "group": "unit",
        "status": "PASS",
        "candidate": dict(candidate),
        "environment": workflow.environment(),
        "log": str(log),
        "log_sha256": workflow.digest(log.read_bytes()),
    }
    workflow.save(record, value)
    workflow.current_evidence(record, "unit")
    candidate["source.py"] = "changed"
    with pytest.raises(ValueError, match="stale"):
        workflow.current_evidence(record, "unit")
    candidate["source.py"] = "hash:mode"
    log.write_bytes(b"changed")
    with pytest.raises(ValueError, match="altered"):
        workflow.current_evidence(record, "unit")


def test_feature_requires_separate_approval(tmp_path):
    # The QUAL-1 approval has the wrong purpose for any application feature delivery.
    payload = workflow.STORE / f"{workflow.POLICY}.payload.json"
    with pytest.raises(ValueError, match="binding mismatch"):
        workflow.feature_authority(payload)


def test_current_lifecycle_and_standards_authority_authenticate():
    payload = workflow.STORE / (
        "005ccfc32a37f8f1ccdcc43b9f4bce6c8c5e5c21897baa97e7950d15023f34ed.payload.json"
    )
    assert workflow.feature_authority(payload)["scope_id"] == "bulk-payments"


def test_lifecycle_rejects_wrong_preimage_and_standards_binding():
    payload = workflow.STORE / (
        "005ccfc32a37f8f1ccdcc43b9f4bce6c8c5e5c21897baa97e7950d15023f34ed.payload.json"
    )
    feature = json.loads(payload.read_text())
    prior = {
        str(Path(name).relative_to(workflow.ROOT)): binding["preimage_sha256"]
        for name, binding in json.loads(
            (
                workflow.STATE / "correlation-settings-aligned-amendment/design-postimages.json"
            ).read_text()
        ).items()
    }
    wrong = dict(prior)
    wrong["docs/features/bulk-payments/api.md"] = "wrong-prior"
    with pytest.raises(ValueError, match="does not extend"):
        workflow.lifecycle_design_overrides(feature, wrong)
    feature["artifacts"]["CODING_STANDARDS.md"] = "wrong-original"
    with pytest.raises(ValueError, match="Standards amendment"):
        workflow.lifecycle_design_overrides(feature, prior)


def test_continuation_preserves_original_deadline_and_cannot_expand_allowance():
    import copy

    state = json.loads((workflow.STATE / "feature-state.json").read_text())
    task = "lifecycle-container-migration"
    original = state["attempts"][task]["deadline_epoch"]
    assert workflow.lifecycle_deadline(state, task) > original
    assert state["attempts"][task]["deadline_epoch"] == original
    altered = copy.deepcopy(state)
    altered["attempts"][task]["continuations"][0]["deadline_epoch"] += 1
    with pytest.raises(ValueError, match="allowance"):
        workflow.lifecycle_deadline(altered, task)
    altered = copy.deepcopy(state)
    altered["attempts"][task]["deadline_epoch"] += 1
    with pytest.raises(ValueError, match="history"):
        workflow.lifecycle_deadline(altered, task)


def test_description_amendment_checks_exact_postimages_and_original_chain(tmp_path, monkeypatch):
    directory = tmp_path / ".harness/project-local-workflow/description-sanitization-amendment"
    directory.mkdir(parents=True)
    changes, original = {}, {}
    for name in ("requirements.md", "api.md", "code-design.md", "scenarios.feature"):
        target = "docs/features/bulk-payments/" + name
        proposed = directory / name
        proposed.write_text("approved postimage: " + name)
        original[target] = "original-" + name
        changes[target] = {
            "preimage_sha256": original[target],
            "proposed_path": str(proposed),
            "postimage_sha256": workflow.digest(proposed.read_bytes()),
        }
    workflow.save(directory / "design-changes.json", changes)
    index = {str(p): workflow.digest(p.read_bytes()) for p in directory.iterdir()}
    workflow.save(directory / "artifacts.json", index)
    approved = {
        "artifacts": {
            str(directory / "artifacts.json"): workflow.digest(
                (directory / "artifacts.json").read_bytes()
            )
        }
    }
    monkeypatch.setattr(workflow, "ROOT", tmp_path)
    monkeypatch.setattr(workflow, "approval", lambda *args: approved)
    expected = {n: b["postimage_sha256"] for n, b in changes.items()}
    assert workflow.description_design_overrides({"artifacts": original}, "feature") == expected
    wrong = dict(original)
    wrong[next(iter(wrong))] = "another-original"
    with pytest.raises(ValueError, match="original design"):
        workflow.description_design_overrides({"artifacts": wrong}, "feature")
    (directory / "api.md").write_text("tampered postimage")
    with pytest.raises(ValueError, match="amendment artifact changed"):
        workflow.description_design_overrides({"artifacts": original}, "feature")


def test_delivery_requires_dependencies_and_complete_checks(tmp_path, monkeypatch):
    payload = tmp_path / "feature.json"
    payload.write_text("{}")
    monkeypatch.setattr(workflow, "STATE", tmp_path / "state")
    monkeypatch.setattr(
        workflow,
        "feature_authority",
        lambda _: {
            "source_manifest": {"source.py": "blob:mode"},
            "check_definitions": [{"id": "unit", "argv": ["unit"]}],
            "tasks": {
                "second": {
                    "depends_on": ["first"],
                    "checks": ["unit"],
                    "owned_paths": ["source.py"],
                },
                "first": {"depends_on": [], "checks": ["unit"], "owned_paths": ["source.py"]},
            },
        },
    )
    monkeypatch.setattr(workflow, "manifest", lambda: {"source.py": "blob:mode"})
    with pytest.raises(FileNotFoundError):
        workflow.record_delivery(payload, "second", {})
    with pytest.raises(ValueError, match="coverage"):
        workflow.record_delivery(payload, "first", {})
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}")
    monkeypatch.setattr(
        workflow, "current_evidence", lambda *_: {"status": "PASS", "argv": ["unit"]}
    )
    record = workflow.record_delivery(payload, "first", {"unit": evidence})
    assert json.loads(record.read_text())["status"] == "DELIVERED_LOCAL"
    with pytest.raises(ValueError, match="already recorded"):
        workflow.record_delivery(payload, "first", {"unit": evidence})
    monkeypatch.setattr(workflow, "manifest", lambda: {"source.py": "different"})
    monkeypatch.setattr(
        workflow,
        "current_evidence",
        lambda *_: (_ for _ in ()).throw(ValueError("stale prerequisite check")),
    )
    with pytest.raises(ValueError, match="stale prerequisite"):
        workflow.record_delivery(payload, "second", {"unit": evidence})


def test_single_pool_alignment_rejects_wrong_prior_and_changed_snapshot(monkeypatch):
    directory = workflow.STATE / "single-pool-alembic-amendment"
    alignment = json.loads((directory / "contract-alignment.json").read_text())
    prior = {name: change["preimage_sha256"] for name, change in alignment["changes"].items()}
    assert workflow.single_pool_design_overrides(prior)
    wrong = dict(prior)
    wrong["docs/features/bulk-payments/api.md"] = "wrong"
    with pytest.raises(ValueError, match="preimages"):
        workflow.single_pool_design_overrides(wrong)
    original = Path.read_bytes
    snapshot = directory / "aligned-contracts" / "api.md"

    def altered(path):
        return b"changed" if path == snapshot else original(path)

    monkeypatch.setattr(Path, "read_bytes", altered)
    with pytest.raises(ValueError, match="snapshot changed"):
        workflow.single_pool_design_overrides(prior)


def test_retry_retains_original_aggregate_deadline():
    state = {
        "attempts": {"middleware": {"started_epoch": 1100, "deadline_epoch": 2300}},
        "attempt_history": {"middleware": [{"started_epoch": 100, "deadline_epoch": 1300}]},
    }
    assert workflow.lifecycle_deadline(state, "middleware") == 1300


def test_crud_deadline_cannot_be_replenished(monkeypatch):
    monkeypatch.setattr(workflow, "crud_authority", lambda: {})
    state = {
        "crud_approval_sha256": workflow.CRUD_AMENDMENT,
        "crud_attempt": {"started_epoch": 100, "deadline_epoch": 1300},
    }
    assert workflow.crud_deadline(state) == 1300
    state["crud_attempt"]["deadline_epoch"] = 1400
    with pytest.raises(ValueError, match="aggregate allowance changed"):
        workflow.crud_deadline(state)


def test_application_persistence_boundaries():
    """Keep driver calls/SQL out of business and HTTP layers as code evolves."""
    import ast

    root = Path(__file__).resolve().parents[1]
    for directory in ["app/services", "app/api", "app/crud"]:
        for path in (root / directory).rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name):
                        assert not (
                            node.func.value.id == "connection"
                            and node.func.attr
                            in {"execute", "cursor", "transaction", "commit", "rollback"}
                        ), str(path)
                if isinstance(node, ast.ImportFrom):
                    assert node.module != "app.db.idempotency", str(path)
                    if directory == "app/crud":
                        assert not (node.module or "").startswith(
                            ("app.services", "app.api", "app.schemas")
                        ), str(path)
                if directory != "app/crud" and isinstance(node, ast.Constant):
                    if isinstance(node.value, str):
                        assert (
                            not node.value.lstrip()
                            .upper()
                            .startswith(
                                ("SELECT ", "INSERT INTO ", "UPDATE FIRMS ", "DELETE FROM ")
                            )
                        ), str(path)
    assert not (root / "app/db/idempotency.py").exists()
