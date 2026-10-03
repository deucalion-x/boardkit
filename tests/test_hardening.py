"""The boundary: what boardkit sends to GitHub, and where it writes on disk.

Both take values out of a committed configuration file, so both are only as
well-behaved as the encoding and the path handling around them.
"""
import pathlib
import subprocess

import pytest

from boardkit.config import Config
from boardkit.gh import BoardError, quoted

CONFIG = Config(root=pathlib.Path("/repo"), owner="octo", repo="octo/widgets")


def test_a_quote_is_escaped_rather_than_ending_the_string():
    """The failure this exists to prevent: a quote in a value closed the
    literal early and the rest of it became GraphQL syntax."""
    assert quoted('needs "design"') == '"needs \\"design\\""'


def test_a_backslash_survives_being_sent():
    assert quoted("a\\b") == '"a\\\\b"'


def test_a_newline_becomes_an_escape_not_a_line_break():
    """An issue body carries newlines, and a raw one is not legal inside a
    GraphQL string. The hand-rolled escape this replaced did not cover it."""
    assert quoted("one\ntwo") == '"one\\ntwo"'


def test_a_control_character_is_encoded():
    assert quoted("a\tb") == '"a\\tb"'


def test_nothing_is_a_quoted_empty_string_rather_than_a_bare_word():
    assert quoted(None) == '""' and quoted("") == '""'


def test_a_filter_containing_a_quote_still_makes_one_string(monkeypatch):
    from boardkit import admin
    sent = []
    monkeypatch.setattr(admin, "run_json",
                        lambda args: sent.append(args[-1]) or {"data": {}})
    admin.filter_view("V_1", 'label:"needs design"')
    assert 'filter: "label:\\"needs design\\""' in sent[0]


def test_an_option_description_containing_a_quote_is_encoded():
    from boardkit.admin import rendered
    out = rendered([{"name": 'a "b"', "color": "GRAY", "description": 'say "x"'}])
    assert 'name: "a \\"b\\""' in out and 'description: "say \\"x\\""' in out


def test_a_text_value_with_a_quote_and_a_newline_is_one_literal():
    from boardkit.config import ProjectField
    from boardkit.store import Write
    from boardkit.sync import value_literal
    field = ProjectField(name="blocked", id="F_B", data_type="TEXT")
    out = value_literal(Write(ref="1", field="blocked", value='he said "no"\nagain'),
                        field)
    assert out == '{text: "he said \\"no\\"\\nagain"}'


def test_a_configured_path_stays_under_the_repository():
    assert CONFIG.path("docs/mirror.md").name == "mirror.md"


def test_a_configured_path_climbing_out_of_the_repository_is_refused():
    """`mirror_path` is written to, so a `../` in it would have boardkit
    create a file outside the repo it was pointed at."""
    with pytest.raises(BoardError, match="outside the repository"):
        CONFIG.path("../escaped.md")


def test_an_absolute_configured_path_is_refused_too():
    with pytest.raises(BoardError, match="outside the repository"):
        CONFIG.path("/etc/passwd")


def test_a_path_that_only_looks_like_an_escape_is_allowed():
    """`..` inside a name is not a traversal, and refusing it would be wrong."""
    assert CONFIG.path("docs/v1..v2/notes.md").name == "notes.md"


def test_every_external_process_carries_a_timeout(monkeypatch):
    """A wedged `gh` or `git` would otherwise hang a terminal, or a CI job,
    with no way to tell it apart from a slow board."""
    from boardkit import gh
    seen = []

    def fake(args, **kwargs):
        seen.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake)
    gh.once(["gh", "--version"])
    gh.probe(["git", "--version"])
    gh.git_ok("--version")
    assert seen == [gh.TIMEOUT] * 3 and all(seen)
