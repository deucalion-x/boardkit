"""Stock a board with a small, believable roadmap, to look at rather than test.

The contract run proves the API calls; its board ends up with one draft card,
which shows nothing of what the roadmap preset is for. This fills a board with
a project as a person would run it: a root Initiative over four phases, each
with its tasks, dated around today so Timeline and Initiatives always show
something past, something current and something ahead, and so `board report`
has a done phase, an active one with work in flight, a blocked task and a late
one.

    uv run python scripts/demo.py                  # project 4, boardkit-sandbox
    uv run python scripts/demo.py --project 5 --repo deucalion-x/other

It is rerunnable. An issue already there under the same title is reused, not
duplicated, and its fields are set again — so the dates move with today and a
card dragged elsewhere is put back.

The issues are real, so they go in a repository kept for this
(`boardkit-sandbox`), never in one whose own board would then count them.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
BOARD = [sys.executable, "-m", "boardkit", "--as-me"]

# (title, days from today to start, to target, status, blocked, children)
# A child is (title, start, target, status, blocked). Status "Done" closes it.
ROADMAP = ("Customer portal launch", -35, 95, [
    ("Discovery", -35, -8, [
        ("Interview five customers about their support requests", -35, -25, "Done", ""),
        ("Map the current ticket flow", -28, -18, "Done", ""),
        ("Agree the portal's scope with the client", -18, -8, "Done", ""),
    ]),
    ("Build the portal", -10, 45, [
        ("Set up the project and the hosting", -10, -5, "Done", ""),
        ("Customer sign-in", -6, 6, "In Progress", ""),
        ("Ticket list and detail pages", -4, -1, "In Progress", ""),
        ("Email notifications", 0, 12, "In Review", ""),
        ("Single sign-on with the client's directory", 8, 20, "Todo",
         "Waiting on the client's SSO details"),
        ("Accessibility pass", 30, 45, "Inbox", ""),
    ]),
    ("Pilot with first customers", 40, 70, [
        ("Choose the pilot customers", 40, 45, "Todo", ""),
        ("Run the pilot", 46, 64, "Inbox", ""),
        ("Pilot review with the client", 65, 70, "Inbox", ""),
    ]),
    ("Rollout", 70, 95, [
        ("Train the support team", 70, 80, "Inbox", ""),
        ("Open the portal to every customer", 85, 95, "Inbox", ""),
    ]),
])


def board(where: pathlib.Path, *args: str) -> str:
    said = subprocess.run([*BOARD, *args], cwd=where, capture_output=True,
                          text=True, check=False, timeout=180,
                          env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    output = (said.stdout or "") + (said.stderr or "")
    if said.returncode != 0:
        raise SystemExit(f"`board {' '.join(args)}` failed:\n{output}")
    return output


def titles() -> list[str]:
    title, _, _, phases = ROADMAP
    return [title, *(name for name, *_ in phases),
            *(task[0] for *_, tasks in phases for task in tasks)]


def relist(where: pathlib.Path, owner: str, project: str) -> None:
    """Put back on the board any demo issue the board does not list.

    GitHub can take many minutes to list items added in bulk; one seen on
    2026-10-14 listed none of nineteen for over ten. Adding an issue again is
    harmless — it returns the item it already has — and is what got them
    listed. Then wait, because a write to an unlisted item is refused.
    """
    for attempt in range(24):
        rows = json.loads(board(where, "json"))
        unlisted = [row for row in rows if row["title"] in titles()
                    and not row["on_board"]]
        if not unlisted:
            return
        if attempt == 0:
            print(f"  {len(unlisted)} demo issue(s) not listed on the board; adding again")
            for row in unlisted:
                subprocess.run(["gh", "project", "item-add", project, "--owner", owner,
                                "--url", row["url"]], capture_output=True, check=True)
        time.sleep(5)
        board(where, "sync")
    raise SystemExit("GitHub still does not list every demo issue on the board; "
                     "run again in a few minutes.")


def day(offset: int) -> str:
    return (datetime.datetime.now().astimezone().date() + datetime.timedelta(days=offset)).isoformat()


class Demo:
    def __init__(self, where: pathlib.Path):
        self.where = where
        rows = json.loads(board(where, "json"))
        self.known = {row["title"]: row for row in rows if row.get("number")}

    def issue(self, title: str, kind: str, parent: int, start: int, target: int,
              status: str, blocked: str = "") -> int:
        row = self.known.get(title)
        values = {"status": status, "start": day(start), "target": day(target),
                  "blocked": blocked}
        if row is None:
            said = board(self.where, "add", title, "--body",
                         "Demo item made by scripts/demo.py.", f"type={kind}",
                         *([f"parent={parent}"] if parent else []),
                         *(f"{name}={value}" for name, value in values.items() if value))
            url = next(word for word in said.split() if "/issues/" in word)
            number = int(url.rstrip("/").rsplit("/", 1)[-1])
            print(f"  made #{number} {title}")
        else:
            number = int(row["number"])
            if row.get("closed") and status != "Done":
                board(self.where, "reopen", str(number))
            for name, value in values.items():
                if (row.get(name) or "") != value:
                    board(self.where, "set", str(number), name, value)
            print(f"  kept #{number} {title}")
        if status == "Done" and not (row or {}).get("closed"):
            board(self.where, "close", str(number))
        return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--owner", default="deucalion-x")
    parser.add_argument("--repo", default="deucalion-x/boardkit-sandbox")
    parser.add_argument("--project", default="4")
    args = parser.parse_args(argv)

    where = pathlib.Path(tempfile.mkdtemp(prefix="boardkit-demo-"))
    (where / "boardkit.toml").write_text(
        f'owner = "{args.owner}"\nowner_type = "org"\nrepo = "{args.repo}"\n'
        f'project = "{args.project}"\n\n[board]\npreset = "roadmap"\n',
        encoding="utf-8")
    board(where, "admin", "link")
    board(where, "sync")
    relist(where, args.owner, args.project)
    demo = Demo(where)

    title, start, target, phases = ROADMAP
    root = demo.issue(title, "Initiative", 0, start, target, "In Progress")
    for name, begins, ends, tasks in phases:
        finished = all(status == "Done" for *_, status, _ in tasks)
        under_way = any(status != "Inbox" for *_, status, _ in tasks)
        phase = demo.issue(name, "Initiative", root, begins, ends,
                           "Done" if finished else "In Progress" if under_way else "Inbox")
        for task, task_start, task_target, status, blocked in tasks:
            demo.issue(task, "Task", phase, task_start, task_target, status, blocked)

    board(where, "sync")
    print()
    print(board(where, "report"))
    print(board(where, "admin", "checklist"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
