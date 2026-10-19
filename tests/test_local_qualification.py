"""Bounded local commands, output redaction and explicit failure results."""

import sys

from scripts.project_workflow import run_command


def test_nonzero_is_failure_and_sensitive_output_redacted(tmp_path):
    secret = "QUAL1-CANARY-DO-NOT-RETAIN"
    result = run_command(
        [sys.executable, "-c", f"print({secret!r}); raise SystemExit(7)"],
        tmp_path,
        secrets=(secret,),
    )
    assert result["status"] == "FAIL"
    assert result["exit_code"] == 7
    assert secret.encode() not in (tmp_path / "command.log").read_bytes()
    assert b"[REDACTED]" in (tmp_path / "command.log").read_bytes()
    assert secret.encode() not in (tmp_path / "command.json").read_bytes()


def test_deadline_is_failure(tmp_path):
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, timeout=0.1
    )
    assert result["status"] == "FAIL"
    assert result["reason"] == "timeout"
    assert result["elapsed_seconds"] < 5


def test_output_exhaustion_is_failure(tmp_path):
    result = run_command([sys.executable, "-c", 'print("x" * 10000)'], tmp_path, max_output=100)
    assert result["status"] == "FAIL"
    assert (tmp_path / "command.log").stat().st_size <= 100


def test_success_has_digest_and_no_unbounded_wait(tmp_path):
    result = run_command([sys.executable, "-c", 'print("done")'], tmp_path)
    assert result["status"] == "PASS"
    assert len(result["log_sha256"]) == 64
