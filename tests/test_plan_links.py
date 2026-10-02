import pathlib

from boardkit.config import Config
from boardkit.plan_links import patterns

CONFIG = Config(root=pathlib.Path("/repo"), owner="octo", project="4",
                repo="octo/places", docs_prefix="docs/")
BARE, LINKED, UNPINNED, BLOB = patterns(CONFIG)


def test_a_bare_path_in_backticks_is_a_candidate():
    assert BARE.findall("see `docs/places/plan.md` for the rule") == [("docs/places/plan.md", "")]


def test_an_anchor_travels_with_the_path():
    assert BARE.findall("`docs/a.md#why`") == [("docs/a.md", "#why")]


def test_an_already_linked_path_is_left_alone():
    assert BARE.findall(f"[`docs/a.md`]({BLOB}/abc1234/docs/a.md)") == []


def test_a_path_outside_the_configured_prefix_is_not_touched():
    assert BARE.findall("`src/places/map.py` and `notes/a.md`") == []


def test_a_sha_pinned_link_is_recognised_for_verification():
    assert LINKED.findall(f"({BLOB}/abc1234def/docs/a.md)") == [("abc1234def", "docs/a.md")]


def test_a_branch_pinned_link_is_reported_rather_than_accepted():
    assert UNPINNED.findall(f"({BLOB}/main/docs/a.md)") == [("main", "docs/a.md")]
    assert UNPINNED.findall(f"({BLOB}/abc1234def/docs/a.md)") == []


def test_the_prefix_is_configurable():
    bare, _, _, _ = patterns(
        Config(root=pathlib.Path("/repo"), owner="o", project="1", repo="o/r",
               docs_prefix="documentation/"))
    assert bare.findall("`documentation/a.md`") == [("documentation/a.md", "")]
