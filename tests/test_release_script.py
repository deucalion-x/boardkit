"""The parts of scripts/release.py that change files, checked without git."""
from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "release.py"


@pytest.fixture(scope="module")
def release():
    spec = importlib.util.spec_from_file_location("release", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unreleased_becomes_the_version_and_a_new_empty_unreleased(release):
    before = "# Changelog\n\n## Unreleased\n\n### Fixes\n- a fix\n\n## 0.3.23 — 2026-10-16\n"
    after = release.with_heading(before, "0.3.24", "2026-10-17")
    assert after == ("# Changelog\n\n## Unreleased\n\n## 0.3.24 — 2026-10-17\n\n"
                     "### Fixes\n- a fix\n\n## 0.3.23 — 2026-10-16\n")


def test_nothing_unreleased_is_refused(release):
    with pytest.raises(release.Refused, match="nothing under"):
        release.with_heading("## Unreleased\n\n## 0.3.23 — 2026-10-16\n", "0.3.24", "d")


def test_the_plugin_version_is_set_and_the_rest_kept(release):
    manifest = json.dumps({"name": "boardkit", "version": "0.3.23", "author": {"name": "x"}})
    assert json.loads(release.with_version(manifest, "0.3.24")) == {
        "name": "boardkit", "version": "0.3.24", "author": {"name": "x"}}


def test_the_contract_row_is_found_by_version(release):
    table = "| Version | Run | Covered |\n|---|---|---|\n| 0.3.24 | skipped | x |\n"
    assert release.recorded(table, "0.3.24")
    assert not release.recorded(table, "0.3.2")
