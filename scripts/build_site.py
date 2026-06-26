"""Build the public Cognition site."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANDING_DIR = ROOT / "web" / "landing"


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    run(["npm", "--prefix", str(LANDING_DIR), "ci"])
    run(["npm", "--prefix", str(LANDING_DIR), "run", "build"])
    run(["mkdocs", "build", "--config-file", "mkdocs.yml", "--strict"])
    run(["mkdocs", "build", "--config-file", "mkdocs.learn.yml", "--strict"])


if __name__ == "__main__":
    main()
