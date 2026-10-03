"""Refuse a release tag that differs from the package metadata."""

from __future__ import annotations

import os
import pathlib
import tomllib

tag = os.environ["RELEASE_TAG"]
metadata = tomllib.loads(pathlib.Path("pyproject.toml").read_text(encoding="utf-8"))
expected = f"v{metadata['project']['version']}"
if tag != expected:
    raise SystemExit(f"tag {tag} does not match {expected}")
