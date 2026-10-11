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
    uv run python scripts/contract.py --project 18  # reuse a standing board
    uv run python scripts/contract.py --seed 20     # stock it with dummy cards

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
import tomllib

from boardkit.setup import rendered

ROOT = pathlib.Path(__file__).resolve().parent.parent
# The checkout's own code, not whatever `board` resolves to on PATH — which on
# a machine that also has boardkit installed is the installed copy, and the
# point of this run is to exercise what is about to be released.
# `--as-me` because the throwaway boards belong to no repository's focused
# token: the run creates and deletes projects, which is the person's own power.
BOARD = [sys.executable, "-m", "boardkit", "--as-me"]

# The canonical starter template supplies the board itself: columns and views.
# These additional fields make its live acceptance run cover every type
# `board admin apply` can create. The status field from the template covers
# SINGLE_SELECT once already; `area` covers its explicit field form too.
CONTRACT_FIELDS = """\
[board.fields.area]
data_type = "SINGLE_SELECT"
options = [
    { name = "core", description = "the library itself" },
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
"""

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
    # GitHub stores every number as a float and hands `3` back as `3.0`. Found
    # by this run on the first day there was anything to find it with, and it
    # was not only cosmetic: a write is compared to the snapshot as text, so
    # `size=3` could never be seen as already true and was re-sent on every
    # push. `board.as_text` now drops the `.0`, which is what this asserts —
    # written as a whole number, read back as one.
    "size": ("3", "3"),
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


def declaration(owner: str, repo: str, title: str, project: str = "") -> str:
    """The canonical starter config, extended only with contract fields.

    Pure, so a unit test can parse it and check it still declares one field of
    every creatable type without creating anything.

    `project` names a board that already exists, so a run can be pointed at a
    standing acceptance board instead of creating another one. It goes in
    beside the other top-level keys, which in TOML means before `[board]`.
    """
    settings = {"owner": owner, "repo": repo, "project_name": title}
    config = rendered(settings, exists=False) + "\n" + CONTRACT_FIELDS
    if project:
        marker = f'project_name = "{title}"'
        assert marker in config, "the template stopped naming the project"
        config = config.replace(marker, f'{marker}\nproject = "{project}"', 1)
    return config


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
    declaration = tomllib.loads(
        (where / "boardkit.toml").read_text(encoding="utf-8"))
    wanted_columns = declaration["board"]["status_order"]
    declared_views = declaration["board"]["views"]
    wanted_views = [view["name"] for view in declared_views]
    fields = {field["name"]: field for field in state["fields"]}
    for name in ("status", "area", "tags", "note", "size", "due", "sprint",
                 "blocked"):
        held(name in fields and bool(fields[name]["id"]),
             f"`{name}` exists on the board with an id")
    # GitHub seeds Todo, In Progress and Done. The template reuses those
    # options by name (case-insensitively) and adds Inbox and In Review, so the
    # resulting order should exactly match the template without duplicate
    # spellings.
    columns = list(fields["status"]["options"])
    held(fields["status"]["data_type"] == "SINGLE_SELECT"
         and columns == wanted_columns,
         f"the template columns arrived in order: {columns}")
    held(fields["tags"]["data_type"] == "MULTI_SELECT"
         and len(fields["tags"]["option_ids"]) == 3,
         "the multi select arrived with all three options and their ids")
    held(fields["sprint"]["data_type"] == "ITERATION",
         "the iteration field arrived as an iteration")
    held(state["views"] == wanted_views,
         f"only the template views arrived: {wanted_views}")
    kind = "organization" if declaration.get("owner_type") == "org" else "user"
    query = (f"{{ {kind}(login: {json.dumps(declaration['owner'])}) "
             f"{{ projectV2(number: {state['project_number']}) "
             "{ views(first: 100) { nodes { name layout filter } } } } }")
    payload = json.loads(gh("api", "graphql", "-f", f"query={query}"))
    live_views = [(node["name"], node["layout"], node.get("filter") or "")
                  for node in payload["data"][kind]["projectV2"]["views"]["nodes"]]
    wanted_live_views = [
        (view["name"], f"{view.get('layout', 'TABLE').upper()}_LAYOUT",
         view.get("filter", ""))
        for view in declared_views]
    held(live_views == wanted_live_views,
         f"the template view layouts arrived: {wanted_live_views}")


def round_trip(where: pathlib.Path, ref: str, what: str,
               skip: tuple[str, ...] = ()) -> None:
    """Write one value of every writable type, then read each one back."""
    fields = {name: pair for name, pair in ROUND_TRIP.items()
              if name not in skip}
    for field, (written, _) in fields.items():
        board(where, "set", ref, field, written)
    board(where, "sync")
    state = snapshot(where)
    for field, (_, wanted) in fields.items():
        found = value(state, ref, field)
        held(found == wanted,
             f"{what} {field} reads back as {wanted!r}"
             + ("" if found == wanted else f" — got {found!r}"))
    check_queue(where, f"writing every field type on {ref}")
    # Writing the same values again: every one is already true remotely, so
    # `plan` has nothing to send and the queue has to empty all the same.
    for field, (written, _) in fields.items():
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


# What a seeded card carries, cycled so the board has variety to look at:
# every column occupied, both areas, a spread of sizes and some empties.
SEED_AREAS = ("core", "docs", "")
SEED_SIZES = ("1", "2", "3", "5", "")
SEED_TAGS = ("red", "red, blue", "")


def seed(where: pathlib.Path, count: int, columns: tuple[str, ...]) -> None:
    """Fill the board with dummy drafts, spread across every column.

    Drafts rather than issues, so a board stocked for eyeballing leaves no
    residue in the repository. Drafts are held in the intake column by policy,
    so each one is moved with `--anyway` — the same documented way through the
    policy that `check_draft_policy` asserts.
    """
    # Every card is created first and the board read once, before a single
    # field is written. GitHub puts a new draft in its own default column, so
    # a write decided against a snapshot taken before the card existed is
    # rejected as a concurrent change — correctly. One sync here is what makes
    # the writes below decide against what the board actually holds.
    refs = []
    for index in range(count):
        column = columns[index % len(columns)]
        said = board(where, "add", "--draft",
                     f"Dummy {index + 1:02d} — {column.lower()} card")
        ref = next((word for word in said.split()
                    if word.startswith("~")), "")
        held(bool(ref), f"dummy {index + 1} is on the board as {ref}")
        refs.append((ref, column))
    board(where, "sync")
    for index, (ref, column) in enumerate(refs):
        board(where, "set", ref, "status", column, "--anyway")
        board(where, "set", ref, "area", SEED_AREAS[index % len(SEED_AREAS)])
        board(where, "set", ref, "size", SEED_SIZES[index % len(SEED_SIZES)])
        board(where, "set", ref, "tags", SEED_TAGS[index % len(SEED_TAGS)])
    board(where, "sync")
    state = snapshot(where)
    held(len(state["items"]) >= count,
         f"the board carries the {count} seeded cards")
    check_queue(where, f"seeding {count} dummy cards")


def check_draft_policy(where: pathlib.Path, ref: str) -> None:
    """A draft stays in the intake column until it is promoted or forced.

    `status` is therefore left out of the draft's round trip and written here
    instead: once to be refused with the guidance that names `promote`, then
    with `--anyway`, which is the documented way through. That keeps every
    writable type covered on the draft and records the policy at the same time.
    """
    written, wanted = ROUND_TRIP["status"]
    said = board(where, "set", ref, "status", written, expect_code=1)
    held("drafts stay in" in said and "promote" in said,
         "setting a column on a draft is refused, pointing at `promote`")
    board(where, "set", ref, "status", written, "--anyway")
    board(where, "sync")
    held(value(snapshot(where), ref, "status") == wanted,
         f"`--anyway` writes the draft's status, which reads back as {wanted!r}")
    check_queue(where, f"forcing a column onto the draft {ref}")


def check_refusal(where: pathlib.Path, ref: str) -> None:
    """Writing the one type boardkit does not write is refused, with a reason.

    Asserted rather than skipped: an iteration field it can create and cannot
    set is a real edge of the tool, and it should fail loudly here the day
    that changes in either direction.
    """
    said = board(where, "set", ref, UNWRITABLE, "Sprint 1", expect_code=1)
    held("cannot write" in said,
         f"a write to the {UNWRITABLE} field is refused with a reason")


def titled(owner: str, number: str) -> str:
    """The name the standing board already carries."""
    seen = json.loads(gh("project", "view", number, "--owner", owner,
                         "--format", "json"))
    return str(seen.get("title", ""))


def run(owner: str, repo: str, keep: bool, with_issue: bool,
        standing: str = "", stock: int = 0) -> int:
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
    title = titled(owner, standing) if standing else f"boardkit contract {stamp}"
    where = pathlib.Path(tempfile.mkdtemp(prefix="boardkit-contract-"))
    (where / "boardkit.toml").write_text(
        declaration(owner, repo, title, project=standing), encoding="utf-8")
    number, issue = standing, ""
    try:
        if standing:
            # A board that already exists is brought to the template rather
            # than built from it: `--reconcile` is what makes the declared
            # views win, which is the point of pointing a run at a board an
            # earlier run already shaped.
            print(f"\n== the standing board ({title}, project {standing})")
            board(where, "admin", "apply", "--reconcile")
            held(True, f"the project was reused (number {number})")
        else:
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

        if stock:
            print(f"\n== stocking the board with {stock} dummy cards")
            declared = tomllib.loads(
                (where / "boardkit.toml").read_text(encoding="utf-8"))
            seed(where, stock, tuple(declared["board"]["status_order"]))

        print("\n== a draft card, every writable field type")
        said = board(where, "add", "--draft", "A card the contract run made")
        draft = next((word for word in said.split()
                      if word.startswith("~")), "")
        held(bool(draft), f"the draft is on the board as {draft}")
        round_trip(where, draft, "the draft's", skip=("status",))

        print("\n== a draft asked to leave the intake column")
        check_draft_policy(where, draft)

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

        built = f"reused project {number}" if standing else "created a project"
        stocked = f", stocked {stock} dummy cards" if stock else ""
        print(f"\nPASS  {built}, read its shape back{stocked}, wrote and read "
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
        if keep or standing:
            print(f"\nKept project {number} and {where}."
                  + (f" Kept issue #{issue}." if issue else ""))
            if standing and not keep:
                # The board outlives the run, so only the run's own residue
                # goes: the issue it filed and the temporary checkout.
                cleanup(owner, repo, "", issue, where)
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
    parser.add_argument("--repo", default="deucalion-x/boardkit",
                        help="the repository the throwaway issue is created in")
    parser.add_argument("--keep", action="store_true",
                        help="leave the project, issue and temp directory behind")
    parser.add_argument("--no-issue", action="store_true",
                        help="drafts only; creates nothing in the repository")
    parser.add_argument("--project", default="", metavar="NUMBER",
                        help="reconcile this standing board instead of "
                             "creating one; it is never deleted")
    parser.add_argument("--seed", type=int, default=0, metavar="COUNT",
                        help="stock the board with this many dummy drafts, "
                             "spread across every column")
    args = parser.parse_args(argv)
    return run(args.owner, args.repo, args.keep, not args.no_issue,
               standing=args.project, stock=args.seed)


if __name__ == "__main__":
    sys.exit(main())
