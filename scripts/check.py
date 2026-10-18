"""Run deterministic foundation checks from the repository root."""

import subprocess
import sys


def main() -> None:
    commands = [
        ["uv", "lock", "--check"],
        [sys.executable, "-m", "ruff", "format", "--check", "."],
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "mypy"],
    ]
    for command in commands:
        subprocess.run(command, check=True, timeout=120)


if __name__ == "__main__":
    main()
