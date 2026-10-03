"""Run every GitHub mutation boardkit sends against a throwaway project.

The test suite mocks `gh`, so it asserts the document boardkit *would* send
and never that GitHub accepts it. That leaves a whole class of failure
invisible: a field shape that changed, an option list sent in a form the API
no longer takes, an escaping fix that altered the bytes of every mutation.
This script is the other half — unmocked, against a project it creates and
deletes, so nothing it does touches the real board.

    uv run python scripts/contract.py             # create, exercise, clean up
    uv run python scripts/contract.py --keep      # leave the project for a look
    uv run python scripts/contract.py --no-issue  # drafts only, no repo residue

It is deliberately not a pytest test. It needs a token that can create and
delete projects, it costs rate-limit points, and an unmocked run against a
live API is flaky by nature — a flaky blocking gate gets ignored, which is
worse than not having one. So it is a command a release runs by hand, and
`docs/releasing.md` records when it last ran.

What it does not cover is said out loud at the end rather than left to be
inferred from what is missing.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
# The checkout's own code, not whatever `board` resolves to on PATH — which on
# a machine that also has boardkit installed is the installed copy, and the
# point of this run is to exercise what is about to be released.
BOARD = [sys.executable, "-m", "boardkit"]

# One field of every type `board admin apply` can create, named as config
# spells them. The status field comes from `status_order`, so SINGLE_SELECT is
# covered twice over — once by the columns and once by `area`, which is the
# shape a repo actually declares.
DECLARED = """\
owner = "{owner}"
repo = "{repo}"
project_name = "{title}"

[board]
status_order = ["Inbox", "Todo", "Done"]

[board.fields.area]
data_type = "SINGLE_SELECT"
options = [
    {{ name = "core", description = "the library itself" }},
    "docs",
]

[board.fields.tags]
data_type = "MULTI_SELECT"
options = ["red", "green", "blue"]

[board.fields.note]
data_type = "TEXT"

[board.fields.size]
data_type = "NUMBER"

[board.fields.due]
data_type = "DATE"

[board.fields.sprint]
data_type = "ITERATION"

[[board.views]]
name = "Open work"
layout = "BOARD"
fields = ["status", "area"]
filter = "-status:Done"
"""

# The columns `DECLARED` asks for, and what GitHub seeds a new project with
# anyway. `Todo` and `Done` are in both, so the run covers an option matched
# by name and kept, one created, and one the board had that config does not
# name — which `align` keeps rather than deleting the items in it.
COLUMNS = ["Inbox", "Todo", "Done"]
SEEDED = ["Todo", "In Progress", "Done"]

# Field -> the value to write, and the value to expect back. A single select
# and a multi select are written by option name, the rest by value. The text
# value carries a quote and a backslash on purpose: that is the escaping the
# mocked suite can only assert about the document, never about what GitHub
# does with it.
ROUND_TRIP = {
    "status": ("Todo", "Todo"),
    "area": ("docs", "docs"),
    "tags": ("red, blue", "red, blue"),
    "note": ('a quote " and a backslash \\', 'a quote " and a backslash \\'),
    # GitHub stores every number as a float and hands it back as one, so `3`
    # written reads as `3.0` — a listing shows `3.0` for a point estimate, and
    # `sync.plan` never sees a re-set of the same value as already true. Found
    # by this run, on the first day there was anything to find it with; the
    # expectation here is the truth rather than the intent.
    "size": ("3", "3.0"),
    "due": ("2026-01-15", "2026-01-15"),
}

# Declared so `admin apply` creates it, and left out of the round trip because
# `sync.value_literal` refuses it. The script asserts the refusal instead, so
# the boundary is recorded rather than silently untested.
UNWRITABLE = "sprint"

# What a cleared value reads as in the snapshot: `board.MISSING`, inlined so
# this script stays a caller of the CLI rather than an importer of its guts.
UNSET = "-"
CLEARABLE = ("note", "size", "due", "area", "tags")


def declaration(owner: str, repo: str, title: str) -> str:
    """The config the throwaway board is built from.

    Pure, so a unit test can parse it and check it still declares one field of
    every creatable type without creating anything.
    """
    return DECLARED.format(owner=owner, repo=repo, title=title)


class Failed(RuntimeError):
    """A step that did not hold. Printed without a traceback."""


def board(where: pathlib.Path, *args: str, expect_code: int = 0) -> str:
    """One `board` command in the throwaway checkout, with its output.

    `cwd` is what chooses the configuration, so every call lands on the
    throwaway board and writes its state under the temporary directory. The
    real `.boardkit/` is never opened.
    """
    said = subprocess.run(
        [*BOARD, *args], cwd=where, capture_output=True, text=True,
        check=False, timeout=180,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    output = (said.stdout or "") + (said.stderr or "")
    print(f"  $ board {' '.join(args)}")
    for line in output.splitlines():
        print(f"    {line}")
    if said.returncode != expect_code:
        raise Failed(f"`board {' '.join(args)}` exited {said.returncode}, "
                     f"expected {expect_code}")
    return output


def gh(*args: str) -> str:
    said = subprocess.run(["gh", *args], capture_output=True, text=True,
                          check=False, timeout=180)
    if said.returncode != 0:
        raise Failed(f"`gh {' '.join(args)}` failed: "
                     f"{(said.stderr or said.stdout).strip()}")
    return said.stdout


def snapshot(where: pathlib.Path) -> dict:
    """The pulled state, read from disk rather than from printed output.

    It carries the ids and option ids a write needs, so asserting against it
    is asserting against what GitHub actually returned.
    """
    return json.loads(
        (where / ".boardkit" / "state.json").read_text(encoding="utf-8"))


def value(state: dict, ref: str, field: str) -> str:
    """One field of one item, addressed the way the CLI addresses it."""
    for item in state["items"]:
        number, identity = item.get("number") or 0, item.get("id", "")
        name = str(number) if number else f"~{identity[-6:]}"
        if name == ref:
            return item.get("fields", {}).get(field, "")
    raise Failed(f"{ref} is not in the snapshot")


def queued(where: pathlib.Path) -> list[dict]:
    """What is still waiting to be pushed. Empty is the only healthy answer
    after a push that reported no failure and no rejection."""
    pending = where / ".boardkit" / "pending.json"
    return json.loads(pending.read_text(encoding="utf-8")) if pending.exists() else []


def held(condition: bool, said: str) -> None:
    if not condition:
        raise Failed(said)
    print(f"  ok  {said}")


def check_queue(where: pathlib.Path, after: str) -> None:
    """Nothing is left pending once a write has landed.

    Asserted because a write that `plan` found already true remotely used to
    stay queued forever: it is in neither the applied nor the lost list, so
    the queue was never cleared of it, and nothing could ever clear it since
    it can neither land nor be rejected.
    """
    left = queued(where)
    held(left == [],
         f"the queue is empty after {after}"
         + ("" if not left else
            f" — {len(left)} left: "
            + ", ".join(f"{w.get('field')}={w.get('value')!r}" for w in left)))


def check_shape(where: pathlib.Path) -> None:
    """Every field, option and view arrived as declared.

    Read back from GitHub rather than from what was sent: `admin apply -n`
    reports what is still missing, and a converged board has nothing. That one
    line is also the convergence claim `apply` makes — run it twice, and the
    second run finds nothing to do.
    """
    again = board(where, "admin", "apply", "-n")
    held("nothing missing." in again,
         "a second `admin apply` finds every field, option and view present")

    state = snapshot(where)
    fields = {field["name"]: field for field in state["fields"]}
    for name in ("status", "area", "tags", "note", "size", "due", "sprint",
                 "blocked"):
        held(name in fields and bool(fields[name]["id"]),
             f"`{name}` exists on the board with an id")
    # `align` never deletes an option, so a brand-new project keeps the ones
    # GitHub seeds it with that config does not name — they keep their place
    # at the end. The declared columns are therefore a prefix of what the
    # board carries, and asserting equality would assert that boardkit
    # deletes a column with items potentially sitting in it.
    columns = list(fields["status"]["options"])
    held(fields["status"]["data_type"] == "SINGLE_SELECT"
         and columns[:len(COLUMNS)] == COLUMNS,
         f"the declared columns arrived in order, first: {columns}")
    held(set(columns) >= set(SEEDED),
         f"the options GitHub seeded and config does not name are kept, "
         f"at the end: {columns[len(COLUMNS):]}")
    held(fields["tags"]["data_type"] == "MULTI_SELECT"
         and len(fields["tags"]["option_ids"]) == 3,
         "the multi select arrived with all three options and their ids")
    held(fields["sprint"]["data_type"] == "ITERATION",
         "the iteration field arrived as an iteration")
    held("Open work" in state["views"], "the declared view arrived")


def round_trip(where: pathlib.Path, ref: str, what: str) -> None:
    """Write one value of every writable type, then read each one back."""
    for field, (written, _) in ROUND_TRIP.items():
        board(where, "set", ref, field, written)
    board(where, "sync")
    state = snapshot(where)
    for field, (_, wanted) in ROUND_TRIP.items():
        found = value(state, ref, field)
        held(found == wanted,
             f"{what} {field} reads back as {wanted!r}"
             + ("" if found == wanted else f" — got {found!r}"))
    check_queue(where, f"writing every field type on {ref}")
    # Writing the same values again: every one is already true remotely, so
    # `plan` has nothing to send and the queue has to empty all the same.
    for field, (written, _) in ROUND_TRIP.items():
        board(where, "set", ref, field, written)
    check_queue(where, "re-writing values that were already true")


def check_clearing(where: pathlib.Path, ref: str) -> None:
    """An empty value unsets the field. That path is the newest one here."""
    for field in CLEARABLE:
        board(where, "set", ref, field, "")
    board(where, "sync")
    state = snapshot(where)
    for field in CLEARABLE:
        found = value(state, ref, field)
        held(found == UNSET,
             f"cleared {field} reads as unset"
             + ("" if found == UNSET else f" — got {found!r}"))
    check_queue(where, "clearing every value")


def check_refusal(where: pathlib.Path, ref: str) -> None:
    """Writing the one type boardkit does not write is refused, with a reason.

    Asserted rather than skipped: an iteration field it can create and cannot
    set is a real edge of the tool, and it should fail loudly here the day
    that changes in either direction.
    """
    said = board(where, "set", ref, UNWRITABLE, "Sprint 1", expect_code=1)
    held("cannot write" in said,
         f"a write to the {UNWRITABLE} field is refused with a reason")


def run(owner: str, repo: str, keep: bool, with_issue: bool) -> int:
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    title = f"boardkit contract {stamp}"
    where = pathlib.Path(tempfile.mkdtemp(prefix="boardkit-contract-"))
    (where / "boardkit.toml").write_text(declaration(owner, repo, title),
                                         encoding="utf-8")
    number, issue = "", ""
    try:
        print(f"\n== a board from nothing ({title})")
        made = board(where, "admin", "apply")
        number = next((line.split()[2].rstrip(":")
                       for line in made.splitlines()
                       if line.startswith("Created project ")), "")
        held(bool(number), f"the project was created (number {number})")
        board(where, "admin", "link")
        board(where, "sync")

        print("\n== what arrived, read back from GitHub")
        check_shape(where)

        print("\n== a draft card, every writable field type")
        said = board(where, "add", "--draft", "A card the contract run made")
        draft = next((word for word in said.split()
                      if word.startswith("~")), "")
        held(bool(draft), f"the draft is on the board as {draft}")
        round_trip(where, draft, "the draft's")

        print("\n== clearing a value")
        check_clearing(where, draft)

        print("\n== a type boardkit does not write")
        check_refusal(where, draft)

        if with_issue:
            print("\n== a real issue, created and fielded in one go")
            said = board(where, "add", f"Contract run {stamp}",
                         "--body", "Created by scripts/contract.py. Deletable.",
                         "status=Todo")
            url = next((word for word in said.split()
                        if word.startswith("https://")), "")
            issue = url.rstrip("/").rsplit("/", 1)[-1]
            held(issue.isdigit(), f"the issue was created as #{issue}")
            board(where, "sync")
            held(value(snapshot(where), issue, "status") == "Todo",
                 "the field set at creation reads back from the board")
            round_trip(where, issue, f"#{issue}")

        print(f"\nPASS  created a project, read its shape back, wrote and read "
              f"{len(ROUND_TRIP)} field types ({', '.join(sorted(ROUND_TRIP))}), "
              f"cleared {len(CLEARABLE)}, and had {UNWRITABLE} refused"
              f"{', with one issue created and placed' if with_issue else ''}.")
        print("      Not covered: writing an ITERATION field, which "
              "boardkit refuses; renaming or deleting a field, which it never "
              "does; and the mirror and links verbs, which touch git rather "
              "than the board.")
        return 0
    except Failed as stopped:
        print(f"\nFAIL  {stopped}")
        return 1
    finally:
        if keep:
            print(f"\nKept project {number} and {where}."
                  + (f" Kept issue #{issue}." if issue else ""))
        else:
            cleanup(owner, repo, number, issue, where)


def cleanup(owner: str, repo: str, number: str, issue: str,
            where: pathlib.Path) -> None:
    """Delete what the run made, saying so even when a step failed.

    Each deletion is attempted on its own rather than in one block: a project
    left behind because the issue could not be deleted is the outcome worth
    avoiding, since an abandoned project costs points on every later
    `project list` and shows up in the owner's project picker forever.
    """
    print()
    if issue:
        try:
            gh("issue", "delete", issue, "--repo", repo, "--yes")
            print(f"Deleted issue #{issue}.")
        except Failed as refused:
            print(f"Could not delete issue #{issue}: {refused}")
    if number:
        try:
            gh("project", "delete", number, "--owner", owner)
            print(f"Deleted project {number}.")
        except Failed as refused:
            print(f"Could not delete project {number}: {refused}")
    shutil.rmtree(where, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Exercise every board mutation against a throwaway project.")
    parser.add_argument("--owner", default="agerardin",
                        help="who owns the throwaway project")
    parser.add_argument("--repo", default="agerardin/boardkit",
                        help="the repository the throwaway issue is created in")
    parser.add_argument("--keep", action="store_true",
                        help="leave the project, issue and temp directory behind")
    parser.add_argument("--no-issue", action="store_true",
                        help="drafts only; creates nothing in the repository")
    args = parser.parse_args(argv)
    return run(args.owner, args.repo, args.keep, not args.no_issue)


if __name__ == "__main__":
    sys.exit(main())
