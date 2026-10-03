"""Smoke-test an installed distribution without importing from the checkout."""

from __future__ import annotations

import importlib.metadata
import importlib.resources
import subprocess

import boardkit

installed = importlib.metadata.version("boardkit")
assert boardkit.__version__ == installed

package = importlib.resources.files("boardkit")
required = (
    package / "templates" / "board-template.toml",
    package / "templates" / "skills" / "board.md",
    package / "templates" / "skills" / "board-admin.md",
)
missing = [str(path) for path in required if not path.is_file()]
assert not missing, f"distribution is missing package data: {missing}"

completed = subprocess.run(["board", "--help"], capture_output=True, text=True, check=False)
assert completed.returncode == 0, completed.stderr
assert "board sync" in completed.stdout
