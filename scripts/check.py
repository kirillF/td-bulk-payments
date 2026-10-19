"""Run currently delivered source/container checks; later slices extend this list."""

import subprocess
import sys


def main() -> None:
    for group in [
        "baseline-syntax",
        "quality",
        "money-unit",
        "atomic-api",
        "uuid-index",
        "idempotency",
        "contention",
        "resource-failures",
        "observability",
        "container-boot",
        "container-http",
    ]:
        subprocess.run(
            [sys.executable, "scripts/harness_checks.py", group], check=True, timeout=300
        )


if __name__ == "__main__":
    main()
