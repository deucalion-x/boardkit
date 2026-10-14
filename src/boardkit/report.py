"""Where each Initiative stands, counted from the snapshot.

"Where is the project" used to be answered by reading `board json` and
counting by hand, a little differently each time. The counting is generic, so
it lives here once; turning it into prose, or publishing it somewhere, stays
with whoever asked.

An Initiative is a roadmap piece whose sub-issues are the work. One whose own
sub-issues are Initiatives is the project itself rather than a piece of it, so
it heads the report instead of taking a line. Progress is direct sub-issues
done over total, which is what GitHub's own sub-issues progress counts, except
that dropped work — closed as not planned, or as a duplicate — is left out of
both: it was never going to be done, and counting it as either would lie.

Dates are the `start` and `target` fields when the board has them; a board
without them still gets its counts.
"""
from __future__ import annotations

import datetime
import json

from pydantic import BaseModel, ConfigDict

from .board import MISSING, ProjectItem, Shape

INITIATIVE = "Initiative"
START, TARGET = "start", "target"
# Closed for one of these is dropped work, not done work.
DROPPED = {"NOT_PLANNED", "DUPLICATE"}


class Child(BaseModel):
    model_config = ConfigDict(frozen=True)

    number: int
    title: str
    url: str = ""
    status: str = ""
    blocked: str = ""
    target: str = ""


class Line(BaseModel):
    """One Initiative, or the project heading them."""
    model_config = ConfigDict(frozen=True)

    number: int
    title: str
    url: str = ""
    status: str = ""
    start: str = ""
    target: str = ""
    done: int = 0
    total: int = 0
    # None when there is nothing to count: 0/0 is not finished, and printing
    # 0 % or 100 % for it would each claim something untrue.
    percent: int | None = None
    closed: bool = False
    # Work has begun: a sub-issue is done or under way, or the start date has
    # come. An active one is started and not yet finished.
    started: bool = False
    active: bool = False
    in_progress: tuple[Child, ...] = ()
    blocked: tuple[Child, ...] = ()
    # For the project: the Initiatives under it, by number.
    initiatives: tuple[int, ...] = ()


class Report(BaseModel):
    model_config = ConfigDict(frozen=True)

    board: str = ""
    today: str
    projects: tuple[Line, ...] = ()
    initiatives: tuple[Line, ...] = ()
    # Numbers of the active Initiatives, nearest target first.
    active: tuple[int, ...] = ()
    # Open items whose target date has passed, oldest target first.
    overdue: tuple[Child, ...] = ()


def value(item: ProjectItem, name: str) -> str:
    found = item.fields.get(name, "")
    return "" if found == MISSING else found


def dropped(item: ProjectItem) -> bool:
    return item.closed and item.state_reason in DROPPED


def finished(item: ProjectItem) -> bool:
    return item.closed and not dropped(item)


def in_flight(view: Shape) -> set[str]:
    """The columns that mean work is under way.

    The first two columns are intake and ready, the last is done: what lies
    between is started. That is the default board's In Progress and In
    Review, and it holds for any board laid out from intake to done.
    """
    return set(view.columns[2:-1])


def child(item: ProjectItem, view: Shape, blocked_field: str) -> Child:
    return Child(number=item.number, title=item.title, url=item.url,
                 status=value(item, view.status_field),
                 blocked=value(item, blocked_field) if blocked_field else "",
                 target=value(item, TARGET))


def build(items: list[ProjectItem], view: Shape, blocked_field: str = "blocked",
          today: datetime.date | None = None, board: str = "") -> Report:
    """The report, from every issue the snapshot holds — on the board or not,
    open or closed — since a sub-issue left off the board is still work."""
    # The reader's own day: a target is late by their calendar, not UTC's.
    today = today or datetime.datetime.now().astimezone().date()
    stamp = today.isoformat()
    issues = [item for item in items if item.number]
    under: dict[int, list[ProjectItem]] = {}
    for item in issues:
        if item.parent and not dropped(item):
            under.setdefault(item.parent, []).append(item)
    flight = in_flight(view)

    def line(item: ProjectItem) -> Line:
        kids = under.get(item.number, [])
        done = sum(1 for kid in kids if finished(kid))
        going = [kid for kid in kids if not kid.closed
                 and value(kid, view.status_field) in flight]
        start = value(item, START)
        started = bool(done or going or (start and start <= stamp))
        complete = finished(item) or (bool(kids) and done == len(kids))
        return Line(
            number=item.number, title=item.title, url=item.url,
            status=value(item, view.status_field), start=start,
            target=value(item, TARGET), done=done, total=len(kids),
            percent=round(100 * done / len(kids)) if kids else None,
            closed=item.closed, started=started,
            active=started and not complete,
            in_progress=tuple(child(kid, view, blocked_field) for kid in going),
            blocked=tuple(child(kid, view, blocked_field) for kid in kids
                          if not kid.closed and blocked_field
                          and value(kid, blocked_field)))

    pieces = [item for item in issues
              if item.issue_type == INITIATIVE and not dropped(item)]
    numbers = {item.number for item in pieces}
    heads = [item for item in pieces
             if any(kid.number in numbers for kid in under.get(item.number, []))]
    head_numbers = {item.number for item in heads}
    lines = [line(item) for item in pieces if item.number not in head_numbers]
    # By start date, undated last, then by number: the order the roadmap runs.
    lines.sort(key=lambda entry: (not entry.start, entry.start, entry.number))

    projects = []
    for item in heads:
        own = [entry for entry in lines
               if any(kid.number == entry.number for kid in under[item.number])]
        done = sum(entry.done for entry in own)
        total = sum(entry.total for entry in own)
        projects.append(Line(
            number=item.number, title=item.title, url=item.url,
            status=value(item, view.status_field), start=value(item, START),
            target=value(item, TARGET), done=done, total=total,
            percent=round(100 * done / total) if total else None,
            closed=item.closed,
            initiatives=tuple(entry.number for entry in own)))

    active = sorted((entry for entry in lines if entry.active),
                    key=lambda entry: (not entry.target, entry.target, entry.number))
    overdue = sorted(
        (child(item, view, blocked_field) for item in issues
         if not item.closed and value(item, TARGET) and value(item, TARGET) < stamp),
        key=lambda entry: (entry.target, entry.number))
    return Report(board=board, today=stamp, projects=tuple(projects),
                  initiatives=tuple(lines),
                  active=tuple(entry.number for entry in active),
                  overdue=tuple(overdue))


def dates(start: str, target: str) -> str:
    if not start and not target:
        return ""
    return f"{start or '?'} → {target or '?'}"


def progress(entry: Line) -> str:
    share = "" if entry.percent is None else f"{entry.percent} %"
    return f"{entry.done}/{entry.total}  {share:>5}"


def render(report: Report) -> str:
    out = []
    for project in report.projects:
        out.append(f"#{project.number} {project.title}  {progress(project).strip()}"
                   f"  {dates(project.start, project.target)}".rstrip())
    if report.projects:
        out.append("")
    if not report.initiatives:
        out.append("No Initiatives on this board: give the roadmap pieces the "
                   "Initiative type and their work as sub-issues.")
        return "\n".join(out)
    width = max(len(f"#{entry.number} {entry.title[:50]}") for entry in report.initiatives)
    for entry in report.initiatives:
        name = f"#{entry.number} {entry.title[:50]}"
        out.append(f"{name:<{width}}  {progress(entry):>11}  "
                   f"{dates(entry.start, entry.target)}".rstrip())

    by_number = {entry.number: entry for entry in report.initiatives}
    out.append("")
    if not report.active:
        out.append("Active: none started.")
    for number in report.active:
        entry = by_number[number]
        out.append(f"Active: #{entry.number} {entry.title}"
                   f"{f' (target {entry.target})' if entry.target else ''}")
        for kid in entry.in_progress:
            out.append(f"  {kid.status}: #{kid.number} {kid.title}")
        for kid in entry.blocked:
            out.append(f"  blocked: #{kid.number} {kid.title} — {kid.blocked}")
        if not entry.in_progress and not entry.blocked:
            out.append("  nothing in progress, nothing blocked")
    if report.overdue:
        out.append("")
        out.append("Past target:")
        for kid in report.overdue:
            out.append(f"  #{kid.number} {kid.title} — target {kid.target}"
                       f"{f', {kid.status}' if kid.status else ''}")
    return "\n".join(out)


def as_json(report: Report) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False)
