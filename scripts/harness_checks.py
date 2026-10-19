"""Executable slice checks. Unimplemented groups fail instead of silently passing."""

import argparse
import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "baseline-syntax",
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
}


def quality() -> None:
    env = dict(
        os.environ,
        UV_CACHE_DIR=str(ROOT / ".uv-cache"),
        UV_PYTHON_INSTALL_DIR=str(ROOT / ".uv-python"),
    )
    for argv in [
        ["uv", "lock", "--check"],
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--check",
            "app",
            "migrations",
            "scripts",
            "tests",
        ],
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "mypy"],
    ]:
        subprocess.run(argv, cwd=ROOT, check=True, timeout=120, env=env)


def cleanup(run: Path) -> None:
    import json

    # Only coordinator-created journals, all below this exact run root.
    for journal in run.glob("containers/*/ownership.json"):
        value = json.loads(journal.read_text())
        identifier = journal.parent.name
        if value["project"] != "td-bulk-test-" + identifier or not identifier.isalnum():
            raise ValueError("Container ownership mismatch")
        config = journal.parent / "compose.json"
        subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                value["project"],
                "-f",
                str(config),
                "down",
                "--volumes",
                "--remove-orphans",
            ],
            check=True,
            timeout=25,
            stdout=subprocess.DEVNULL,
        )
        # The unique image name belongs to this exact journal; never prune build caches.
        inspected = subprocess.run(
            ["docker", "image", "inspect", value["image"]],
            capture_output=True,
            check=False,
            timeout=5,
        )
        if inspected.returncode == 0:
            image = json.loads(inspected.stdout)[0]
            if image["Config"]["Labels"].get("td-bulk-payments.run") != identifier:
                raise ValueError("Image ownership mismatch")
            subprocess.run(
                ["docker", "image", "rm", value["image"]],
                check=True,
                timeout=5,
                stdout=subprocess.DEVNULL,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group")
    parser.add_argument("run", nargs="?", type=Path)
    args = parser.parse_args()
    if args.group == "cleanup":
        if args.run is None:
            parser.error("cleanup requires owned run directory")
        cleanup(args.run.resolve())
    elif args.group not in GROUPS:
        parser.error("check group is not implemented")
    elif args.group == "baseline-syntax":
        for root in ["app", "migrations", "scripts"]:
            for path in (ROOT / root).rglob("*.py"):
                ast.parse(path.read_text(), filename=str(path))
    elif args.group == "quality":
        quality()
    elif args.group == "observability":
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_observability.py",
                "tests/test_health.py",
                "tests/test_journeys.py",
                "--scenarios=SC-22,SC-23,SC-24,SC-25",
            ],
            cwd=ROOT,
            check=True,
            timeout=200,
        )
    elif args.group == "container-http":
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_container.py::test_final_http_samples",
                "tests/test_journeys.py",
                "--scenarios=SC-18,SC-21",
            ],
            cwd=ROOT,
            check=True,
            timeout=250,
        )
    elif args.group == "contention":
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_concurrency.py",
                "tests/test_payments.py",
                "-k",
                "parallel or opposing",
            ],
            cwd=ROOT,
            check=True,
            timeout=120,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_journeys.py",
                "--scenarios=SC-11,SC-12,SC-13",
            ],
            cwd=ROOT,
            check=True,
            timeout=90,
        )
    elif args.group == "resource-failures":
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_settings.py",
                "tests/test_resource_failures.py",
                "tests/test_payments.py",
                "-k",
                "not parallel and not opposing and not uuid_lookup",
            ],
            cwd=ROOT,
            check=True,
            timeout=150,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_journeys.py",
                "--scenarios=SC-14,SC-15,SC-16,SC-20,SC-29",
            ],
            cwd=ROOT,
            check=True,
            timeout=180,
        )
    elif args.group == "idempotency":
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_idempotency.py",
                "tests/test_payments.py",
                "-k",
                "not parallel and not opposing and not lock_timeout and not uuid_lookup",
            ],
            cwd=ROOT,
            check=True,
            timeout=150,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_journeys.py",
                "--scenarios=SC-7,SC-8,SC-9,SC-10,SC-16",
            ],
            cwd=ROOT,
            check=True,
            timeout=90,
        )
    elif args.group == "money-unit":
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/test_models.py"],
            cwd=ROOT,
            check=True,
            timeout=60,
        )
    elif args.group == "uuid-index":
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests/test_index.py"],
            cwd=ROOT,
            check=True,
            timeout=90,
        )
    elif args.group == "atomic-api":
        selection = (
            "not parallel and not opposing and not lock_timeout "
            "and not replay_and_conflict and not lost_http and not uuid_lookup"
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_core.py",
                "tests/test_migrations.py",
                "tests/test_payments.py",
                "tests/test_journeys.py",
                "--scenarios=SC-1,SC-2,SC-3,SC-4,SC-5,SC-6,SC-14,SC-17,SC-19,SC-21,SC-27,SC-28,SC-30,SC-32,SC-33,SC-35",
                "-k",
                selection,
            ],
            cwd=ROOT,
            check=True,
            timeout=150,
        )
    elif args.group == "container-boot":
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "tests/test_container.py::test_sc26_clean_image_and_repeat_safe_bootstrap",
                "tests/test_migrations.py::test_initial_revision_safety",
                "tests/test_migrations.py::test_startup_rejects_unsupported_revision_without_effects",
                "tests/test_journeys.py",
                "--scenarios=SC-26,SC-31,SC-34,SC-36,SC-37,SC-38",
            ],
            cwd=ROOT,
            check=True,
            timeout=280,
        )


if __name__ == "__main__":
    main()
