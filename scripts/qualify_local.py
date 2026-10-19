"""Real Docker/Testcontainers qualification under the approved trusted-host policy."""

import argparse
import io
import json
import os
import secrets
import sys
import tarfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, cast

from project_workflow import ROOT, policy, run_command, save

LABEL = "td-bulk-payments.qual1-run"

# A qualification-only shell: no application/payment behavior is exercised here.
SHELL = """import json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
import psycopg
with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5) as c:
    assert c.execute("SELECT 42").fetchone()[0] == 42
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body=json.dumps({"python":list(sys.version_info[:2]),"uid":os.getuid(),"sql":42}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass
HTTPServer(("0.0.0.0",8000), Handler).serve_forever()
"""


def cleanup(client: Any, run: Path, *, preserve_sentinel: bool = False) -> None:
    """Reconcile durable run labels; never prune, remove volumes globally or follow names."""
    journal = json.loads((run / "ownership.json").read_text())
    identifier = journal["run_id"]
    if run.name != identifier:
        raise ValueError("Ownership directory/run identity mismatch")
    failures = []
    for container in client.containers.list(all=True, filters={"label": f"{LABEL}={identifier}"}):
        try:
            if container.labels.get(LABEL) != identifier:
                raise ValueError("Container ownership mismatch")
            container.remove(force=True, v=True)
        except Exception as error:
            failures.append(type(error).__name__)
    for network in client.networks.list(filters={"label": f"{LABEL}={identifier}"}):
        try:
            network.reload()
            if network.attrs["Labels"].get(LABEL) != identifier:
                raise ValueError("Network ownership mismatch")
            network.remove()
        except Exception as error:
            failures.append(type(error).__name__)
    tag = journal["image_tag"]
    try:
        image = client.images.get(tag)
    except client_image_missing():
        pass
    else:
        if image.labels.get(LABEL) == identifier:
            try:
                client.images.remove(tag)
            except Exception as error:
                failures.append(type(error).__name__)
        else:
            failures.append("ImageOwnershipMismatch")
    if not preserve_sentinel:
        for sentinel in client.networks.list(filters={"label": f"qual1-sentinel={identifier}"}):
            try:
                sentinel.reload()
                if sentinel.attrs["Labels"].get("qual1-sentinel") != identifier:
                    raise ValueError("Sentinel ownership mismatch")
                sentinel.remove()
            except Exception as error:
                failures.append(type(error).__name__)
    remaining = client.containers.list(all=True, filters={"label": f"{LABEL}={identifier}"})
    remaining_networks = client.networks.list(filters={"label": f"{LABEL}={identifier}"})
    if failures or remaining or remaining_networks:
        save(run / "cleanup.json", {"status": "FAIL", "errors": failures})
        raise RuntimeError("Cleanup uncertain; ownership retained")
    save(run / "cleanup.json", {"status": "PASS", "run_id": identifier})


def client_image_missing() -> type[Exception]:
    from docker.errors import ImageNotFound  # type: ignore[import-untyped]

    return cast(type[Exception], ImageNotFound)


def exercise(*, fail_after_postgres: bool = False) -> None:
    import docker  # type: ignore[import-untyped]
    import psycopg
    from testcontainers.community.postgres import PostgresContainer

    policy()
    if sys.version_info[:2] != (3, 15):
        raise RuntimeError("Qualification requires Python 3.15")
    os.environ["TESTCONTAINERS_RYUK_DISABLED"] = "true"
    os.environ["TC_MAX_TRIES"] = "10"
    os.environ["TC_POOLING_INTERVAL"] = "0.5"
    identifier = uuid.uuid4().hex
    parent = Path(os.environ.get("QUAL1_RUN_DIR", ROOT / ".harness/project-local-qual1/runs"))
    run = parent / identifier
    labels = {LABEL: identifier}
    tag = f"td-bulk-payments-qual1:{identifier}"
    save(run / "ownership.json", {"run_id": identifier, "image_tag": tag, "status": "ACTIVE"})
    client = docker.from_env(timeout=10)
    # A separate resource represents an unrelated workload; cleanup must leave it alive.
    sentinel = client.networks.create(
        f"qual1-sentinel-{identifier}", labels={"qual1-sentinel": identifier}
    )
    succeeded = False
    try:
        info = client.info()
        if info["OSType"] != "linux":
            raise RuntimeError("Linux Docker engine required")
        images = {}
        for name in ["postgres:17", "python:3.15-slim", "ghcr.io/astral-sh/uv:0.13.1"]:
            try:
                image = client.images.get(name)
            except docker.errors.ImageNotFound:
                result = run_command(
                    ["docker", "pull", name],
                    run / name.replace("/", "_").replace(":", "_"),
                    timeout=600,
                )
                if result["status"] != "PASS":
                    raise RuntimeError("Required image unavailable") from None
                image = client.images.get(name)
            images[name] = {"id": image.id, "digests": image.attrs.get("RepoDigests", [])}
        network = client.networks.create(f"qual1-{identifier}", labels=labels)
        password = secrets.token_hex(24)
        postgres = PostgresContainer("postgres:17", driver=None, password=password)
        # Docker SDK accepts loopback binding tuples; Testcontainers' annotation is narrower.
        cast(Any, postgres.ports)["5432"] = ("127.0.0.1", None)
        postgres.with_name(f"qual1-pg-{identifier}")
        postgres.with_kwargs(
            network=network.name,
            labels=labels,
            mem_limit="256m",
            nano_cpus=1_000_000_000,
            pids_limit=128,
            log_config=docker.types.LogConfig(
                type="json-file", config={"max-size": "1m", "max-file": "2"}
            ),
        )
        postgres.start()
        with psycopg.connect(postgres.get_connection_url(), connect_timeout=5) as connection:
            connection.execute("CREATE TABLE qualification (balance INTEGER CHECK (balance >= 0))")
            connection.execute("INSERT INTO qualification VALUES (100)")
            connection.commit()
            try:
                with connection.transaction():
                    connection.execute("UPDATE qualification SET balance = 75")
                    connection.execute("UPDATE qualification SET balance = -1")
            except psycopg.errors.CheckViolation:
                pass
            else:
                raise AssertionError("Deliberately failed SQL did not fail")
            assert connection.execute("SELECT balance FROM qualification").fetchone() == (100,)
            row = connection.execute("SHOW server_version").fetchone()
            assert row is not None
            server = row[0]
            assert server.startswith("17.")
        save(run / "rollback.json", {"rollback_verified": True})
        if fail_after_postgres:
            raise RuntimeError("Injected qualification failure after SQL rollback verification")
        # Explicit tiny build context, never the host/project directory.
        files = {
            "pyproject.toml": (ROOT / "pyproject.toml").read_bytes(),
            "uv.lock": (ROOT / "uv.lock").read_bytes(),
            "shell.py": SHELL.encode(),
        }
        dockerfile = """FROM ghcr.io/astral-sh/uv:0.13.1 AS uv
FROM python:3.15-slim
COPY --from=uv /uv /bin/uv
WORKDIR /fixture
COPY pyproject.toml uv.lock ./
ENV UV_LINK_MODE=copy
RUN uv sync --locked --no-dev --no-managed-python
COPY shell.py ./
USER 10001:10001
CMD ["/fixture/.venv/bin/python", "shell.py"]
"""
        files["Dockerfile"] = dockerfile.encode()
        context = io.BytesIO()
        with tarfile.open(fileobj=context, mode="w") as archive:
            for name, data in files.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        context_path = run / "context.tar"
        context_path.write_bytes(context.getvalue())
        # Docker build runs as a bounded child; context is only these four files.
        result = build_context(context_path, tag, identifier, run)
        if result["status"] != "PASS":
            raise RuntimeError("Qualification image build failed")
        service = client.containers.run(
            tag,
            detach=True,
            labels=labels,
            network=network.name,
            environment={
                "DATABASE_URL": f"postgresql://test:{password}@qual1-pg-{identifier}:5432/test"
            },
            ports={"8000/tcp": ("127.0.0.1", None)},
            read_only=True,
            tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
            mem_limit="256m",
            nano_cpus=1_000_000_000,
            pids_limit=128,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
        )
        service.reload()
        assert postgres.get_wrapped_container().attrs["Image"] == images["postgres:17"]["id"]
        built_image = client.images.get(tag)
        assert service.attrs["Image"] == built_image.id
        images["qualification-service"] = {"id": built_image.id}
        port = service.attrs["NetworkSettings"]["Ports"]["8000/tcp"][0]["HostPort"]
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}", timeout=1) as response:
                    body = json.load(response)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Qualification HTTP startup deadline") from None
                time.sleep(0.1)
        assert body == {"python": [3, 15], "uid": 10001, "sql": 42}
        save(
            run / "runtime.json",
            {
                "python": sys.version,
                "postgres": server,
                "engine_id": info["ID"],
                "images": images,
                "http": body,
                "scope": "qualification fixture only; no SC-26/payment acceptance",
            },
        )
        succeeded = True
    finally:
        try:
            cleanup(client, run, preserve_sentinel=True)
            sentinel.reload()
            save(run / "sentinel.json", {"unrelated_resource_preserved": True})
        finally:
            # Exact newly-created test sentinel, never another workload.
            sentinel.remove()
            client.close()
    if succeeded:
        print(json.dumps({"status": "PASS", "evidence": str(run)}))


def build_context(context: Path, tag: str, identifier: str, run: Path) -> dict[str, Any]:
    # Extract only coordinator-generated members into an owned build directory.
    directory = run / "context"
    directory.mkdir()
    with tarfile.open(context) as archive:
        archive.extractall(directory, filter="data")
    return run_command(
        ["docker", "build", "--label", f"{LABEL}={identifier}", "-t", tag, str(directory)],
        run / "build",
        timeout=600,
    )


def negative() -> None:
    """Exercise actual unavailable-daemon and post-creation failure paths."""
    import docker

    policy()
    parent = Path(os.environ["QUAL1_RUN_DIR"])
    failed = run_command(
        [sys.executable, __file__, "exercise", "--fail-after-postgres"],
        parent / "injected-failure",
        timeout=90,
        env=dict(os.environ, QUAL1_RUN_DIR=str(parent / "post-create")),
    )
    journals = list((parent / "post-create").glob("*/ownership.json"))
    if failed["status"] != "FAIL" or len(journals) != 1:
        raise RuntimeError("Failure injection did not exercise owned resources")
    run = journals[0].parent
    if not json.loads((run / "rollback.json").read_text())["rollback_verified"]:
        raise RuntimeError("Injected failure occurred before SQL verification")
    if json.loads((run / "cleanup.json").read_text())["status"] != "PASS":
        raise RuntimeError("Failure cleanup did not pass")
    client = docker.from_env(timeout=10)
    try:
        cleanup(client, run)  # Demonstrate idempotent recovery after failure.
    finally:
        client.close()
    unreachable = run_command(
        [sys.executable, __file__, "exercise"],
        parent / "unavailable-daemon",
        timeout=30,
        env=dict(
            os.environ,
            DOCKER_HOST="unix:///private/tmp/qual1-nonexistent-docker.sock",
            QUAL1_RUN_DIR=str(parent / "unavailable"),
        ),
    )
    if unreachable["status"] != "FAIL" or unreachable["reason"] != "nonzero_exit":
        raise RuntimeError("Unavailable daemon did not fail closed within deadline")
    save(
        parent / "negative.json",
        {"status": "PASS", "post_creation_failure_cleanup": True, "unavailable_daemon_fails": True},
    )
    print(json.dumps({"status": "PASS", "negative_checks": str(parent / "negative.json")}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["exercise", "cleanup", "negative"])
    parser.add_argument("run", type=Path, nargs="?")
    parser.add_argument("--fail-after-postgres", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "exercise":
            exercise(fail_after_postgres=args.fail_after_postgres)
        elif args.command == "negative":
            negative()
        else:
            import docker

            policy()
            if args.run is None:
                parser.error("cleanup requires exact ownership directory")
            client = docker.from_env(timeout=10)
            try:
                cleanup(client, args.run.resolve())
            finally:
                client.close()
    except Exception as error:
        # Driver exceptions may contain connection details. Store type only.
        print(json.dumps({"status": "FAIL", "error_type": type(error).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
