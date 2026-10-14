"""`board report`: where each Initiative stands, counted from the snapshot."""
import datetime
import json

from boardkit import report
from boardkit.board import ProjectItem, Shape

VIEW = Shape(status_field="status",
             columns=("Inbox", "Todo", "In Progress", "In Review", "Done"))
TODAY = datetime.date(2026, 10, 14)


def issue(number, parent=0, kind="Task", status="Todo", start="", target="",
          blocked="", closed=False, reason="", on_board=True):
    fields = {"status": status, "start": start or "-", "target": target or "-",
              "blocked": blocked or "-"}
    return ProjectItem(number=number, title=f"Item {number}", parent=parent,
                       issue_type=kind, closed=closed, state_reason=reason,
                       on_board=on_board, fields=fields,
                       url=f"https://github.com/o/r/issues/{number}")


def roadmap():
    """The shape of the first project to use this, on 2026-10-14: one root
    Initiative over four, the first started with one task done and one
    blocked, the second with a sub-issue that is not on the board."""
    return [
        issue(25, kind="Initiative", start="2026-10-14", target="2027-01-29"),
        issue(1, 25, "Initiative", start="2026-10-14", target="2026-11-20"),
        issue(2, 1, status="Done", closed=True, reason="COMPLETED"),
        *(issue(n, 1, target="2026-10-27") for n in (3, 4, 5, 7, 8, 9, 10)),
        issue(6, 1, target="2026-10-27", blocked="Waiting on the customer"),
        issue(11, 25, "Initiative", status="Inbox", start="2027-01-15",
              target="2027-01-29"),
        *(issue(n, 11, status="Inbox") for n in (13, 14, 15, 16, 17)),
        issue(18, 11, status="", on_board=False),
        issue(12, 25, "Initiative", status="Inbox"),
        *(issue(n, 12, status="Inbox") for n in (19, 20, 21, 22)),
        issue(26, 25, "Initiative", status="Inbox"),
        *(issue(n, 26, status="Inbox") for n in (23, 24, 27, 28, 29)),
    ]


def counts(built):
    return [(entry.number, entry.done, entry.total, entry.percent)
            for entry in built.initiatives]


def test_one_line_per_initiative_with_the_root_as_the_project():
    built = report.build(roadmap(), VIEW, today=TODAY)
    assert counts(built) == [(1, 1, 9, 11), (11, 0, 6, 0), (12, 0, 4, 0), (26, 0, 5, 0)]
    assert [(p.number, p.done, p.total, p.initiatives) for p in built.projects] == [
        (25, 1, 24, (1, 11, 12, 26))]


def test_the_started_initiative_is_active_with_its_blocked_child():
    built = report.build(roadmap(), VIEW, today=TODAY)
    assert built.active == (1,)
    first = built.initiatives[0]
    assert [(kid.number, kid.blocked) for kid in first.blocked] == [
        (6, "Waiting on the customer")]
    text = report.render(built)
    assert "Active: #1 Item 1 (target 2026-11-20)" in text
    assert "blocked: #6 Item 6 — Waiting on the customer" in text


def test_an_initiative_with_no_sub_issues_is_zero_of_zero_and_not_done():
    built = report.build([issue(40, kind="Initiative", start="2026-10-01")],
                         VIEW, today=TODAY)
    [entry] = built.initiatives
    assert (entry.done, entry.total, entry.percent) == (0, 0, None)
    assert entry.active
    assert "0/0" in report.render(built)


def test_dropped_work_counts_neither_as_done_nor_as_left():
    items = [issue(40, kind="Initiative"),
             issue(41, 40, status="Done", closed=True, reason="COMPLETED"),
             issue(42, 40, closed=True, reason="NOT_PLANNED"),
             issue(43, 40)]
    [entry] = report.build(items, VIEW, today=TODAY).initiatives
    assert (entry.done, entry.total) == (1, 2)


def test_work_in_flight_is_named_under_its_active_initiative():
    items = [issue(40, kind="Initiative"), issue(41, 40, status="In Review"),
             issue(42, 40)]
    built = report.build(items, VIEW, today=TODAY)
    assert built.active == (40,)
    assert [kid.number for kid in built.initiatives[0].in_progress] == [41]
    assert "In Review: #41 Item 41" in report.render(built)


def test_open_items_past_their_target_are_listed_and_closed_ones_are_not():
    items = [issue(40, kind="Initiative"),
             issue(41, 40, target="2026-10-10"),
             issue(42, 40, target="2026-10-01", status="Done", closed=True,
                   reason="COMPLETED"),
             issue(43, 40, target="2026-10-20")]
    built = report.build(items, VIEW, today=TODAY)
    assert [kid.number for kid in built.overdue] == [41]
    assert "Past target:" in report.render(built)


def test_json_carries_every_number_the_text_shows():
    built = report.build(roadmap(), VIEW, today=TODAY, board="https://x/projects/1")
    data = json.loads(report.as_json(built))
    assert data["board"] == "https://x/projects/1"
    assert data["today"] == "2026-10-14"
    first = data["initiatives"][0]
    assert (first["done"], first["total"], first["percent"]) == (1, 9, 11)
    assert (first["start"], first["target"]) == ("2026-10-14", "2026-11-20")
    assert data["active"] == [1]
    assert data["initiatives"][0]["blocked"][0]["number"] == 6
    assert data["projects"][0]["total"] == 24


def test_a_board_without_initiatives_says_how_to_get_them():
    built = report.build([issue(1)], VIEW, today=TODAY)
    assert "No Initiatives" in report.render(built)
