"""Fill a portfolio: one board showing the Initiatives of several boards.

A portfolio is an organisation's Project whose items are the Initiatives of
the repositories its config lists. Each Initiative is still worked on its own
repository's board; the portfolio is where all of them are read side by side.

So nothing is edited on it, and this module is the only thing that writes to
it. One verb converges it, and a daily GitHub Action runs that verb:

- every Initiative of a listed repository is on the portfolio;
- each one's Status, Start, Target and blocked — whichever of the copied
  fields the portfolio has — match its own board. Those are Project fields,
  held per board rather than on the issue, so they have to be copied.
  Sub-issues progress is not: GitHub computes it from the issue itself;
- an issue that is no longer one of those Initiatives leaves it.

What is read is the issue, not the other board. An issue lists the project
items it has, with their values, so the source boards never have to be
named here, and a repository whose board is renumbered goes on working.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import auth
from .admin import locate
from .board import MISSING, key
from .config import Config, ProjectField
from .gh import BoardError, quoted, run_json
from .store import Write
from .sync import VALUE_FRAGMENTS, fields_block, flatten, parse_fields, value_literal
from .trail import note

# The issue type a portfolio collects. GitHub's own name for a roadmap piece,
# and the one every boardkit board files its roadmap under.
INITIATIVE = "Initiative"
# Initiatives read per request. Each carries its project items and their
# values, so a page is priced by those too; fifty keeps it far below GitHub's
# node limit without making a large repository take many requests.
PAGE = 50
# Mutations per document. GitHub takes many in one; a bounded batch keeps one
# refused change from hiding the outcome of hundreds of others.
BATCH = 50
# The kinds of value a copy can carry. Iterations and multi-selects belong to
# one board's own vocabulary, so they are not copied even when named alike.
COPYABLE = ("TEXT", "DATE", "NUMBER", "SINGLE_SELECT")


@dataclass
class Item:
    """An item already on the portfolio."""
    id: str
    content_id: str = ""
    kind: str = ""
    repo: str = ""
    issue_type: str = ""
    number: int = 0
    values: dict = field(default_factory=dict)


@dataclass
class Initiative:
    """An Initiative of a listed repository, with its own board's values."""
    id: str
    repo: str
    number: int
    title: str
    values: dict = field(default_factory=dict)

    @property
    def ref(self) -> str:
        return f"{self.repo.split('/')[-1]}#{self.number}"


def owner_query(config: Config, inner: str) -> str:
    kind = "organization" if config.owner_type == "org" else "user"
    return (f"fragment fieldName on ProjectV2FieldCommon {{ name }} "
            f"{{ {kind}(login: {quoted(config.owner)}) {{ {inner} }} }}")


def fetch_portfolio(config: Config, number: str) -> tuple[str, list[dict], list[Item]]:
    """The portfolio's id, its fields and every item on it, paged to the end.

    Each item carries the repository and type of its issue: whether it
    belongs here is read from the issue itself, never from its absence in a
    listing, so a repository that could not be read loses nothing.
    """
    kind = "organization" if config.owner_type == "org" else "user"
    project_id, fields, items, after = "", [], [], ""
    while True:
        cursor = f", after: {quoted(after)}" if after else ""
        head = f"id {fields_block(config, None)}" if not after else ""
        query = owner_query(config, (
            f"projectV2(number: {number}) {{ {head} "
            f"items(first: {config.page_size}{cursor}) {{ "
            "pageInfo { hasNextPage endCursor } nodes { id type "
            "content { ... on Issue { id number repository { nameWithOwner } "
            "issueType { name } } "
            "  ... on PullRequest { id number repository { nameWithOwner } } } "
            f"fieldValues(first: {config.page_size}) {{ nodes {{{VALUE_FRAGMENTS}}} }} "
            "} } }"))
        payload = run_json(["gh", "api", "graphql", "-f", f"query={query}"])
        project = ((payload.get("data") or {}).get(kind) or {}).get("projectV2")
        if not project:
            raise BoardError(f"GitHub would not show project {number} of "
                             f"{config.owner} to this credential.")
        if not after:
            project_id = project.get("id", "")
            fields = [node for node in project["fields"]["nodes"] if node]
        block = project["items"]
        for node in block["nodes"]:
            if not node:
                continue
            content = node.get("content") or {}
            flat = flatten(node)
            items.append(Item(
                id=node["id"], content_id=content.get("id", ""),
                kind=node.get("type") or "",
                repo=(content.get("repository") or {}).get("nameWithOwner", ""),
                issue_type=(content.get("issueType") or {}).get("name", ""),
                number=content.get("number") or 0,
                values={name: value for name, value in flat.items()
                        if name not in ("id", "content")}))
        if not block["pageInfo"].get("hasNextPage"):
            return project_id, fields, items
        after = block["pageInfo"]["endCursor"]


def fetch_initiatives(repo: str, portfolio_id: str) -> list[Initiative]:
    """The repository's Initiatives, open or closed, with their own values.

    Closed ones too: a finished Initiative is part of the roadmap's history,
    and the portfolio's views choose whether to show it.

    The values are those of every other board the issue is on; the
    portfolio's own are what is being corrected. When two boards disagree,
    the first one GitHub lists wins.
    """
    owner, name = repo.split("/", 1)
    found, after = [], ""
    while True:
        cursor = f", after: {quoted(after)}" if after else ""
        query = ("fragment fieldName on ProjectV2FieldCommon { name } "
                 f"{{ repository(owner: {quoted(owner)}, name: {quoted(name)}) "
                 f"{{ issues(first: {PAGE}{cursor}, "
                 f"filterBy: {{type: {quoted(INITIATIVE)}}}) "
                 "{ pageInfo { hasNextPage endCursor } nodes { id number title "
                 "projectItems(first: 10) { nodes { project { id } "
                 f"fieldValues(first: 50) {{ nodes {{{VALUE_FRAGMENTS}}} }} "
                 "} } } } } }")
        payload = run_json(["gh", "api", "graphql", "-f", f"query={query}"])
        repository = (payload.get("data") or {}).get("repository")
        if not repository:
            raise BoardError(f"cannot read {repo}: it does not exist, or this "
                             f"credential cannot see it")
        block = repository["issues"]
        for node in block["nodes"]:
            if not node:
                continue
            values: dict = {}
            for item in (node.get("projectItems") or {}).get("nodes") or []:
                if not item or (item.get("project") or {}).get("id") == portfolio_id:
                    continue
                for field_name, value in flatten(item).items():
                    if field_name not in ("id", "content") and field_name not in values:
                        values[field_name] = value
            found.append(Initiative(node["id"], repo, node["number"],
                                    node.get("title", ""), values))
        if not block["pageInfo"].get("hasNextPage"):
            return found
        after = block["pageInfo"]["endCursor"]


def copied(config: Config, fields: tuple[ProjectField, ...]) -> list[ProjectField]:
    """The portfolio's fields that are filled from the Initiatives' boards:
    the columns, the declared fields and `blocked`, where the portfolio has
    them and they hold a kind of value a copy can carry."""
    names = [config.status_field, *config.field_names, config.blocked_field]
    wanted = list(dict.fromkeys(key(name) for name in names if name))
    have = {key(f.name): f for f in fields if f.id}
    return [have[name] for name in wanted
            if name in have and have[name].data_type in COPYABLE]


def text(value) -> str:
    """A value as a comparison and a write take it; unset is empty."""
    if value is None or value == MISSING:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


@dataclass
class Plan:
    add: list[Initiative] = field(default_factory=list)
    remove: list[Item] = field(default_factory=list)
    # (initiative, field, value, was); an empty value clears the field.
    set: list[tuple[Initiative, ProjectField, str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def plan(config: Config, fields: tuple[ProjectField, ...], items: list[Item],
         initiatives: list[Initiative]) -> Plan:
    """What brings the portfolio into line. Pure, so the rules are testable
    without a project."""
    repos = set(config.portfolio.repos if config.portfolio else ())
    result = Plan()
    on_board = {item.content_id: item for item in items if item.content_id}
    for item in items:
        # A draft is someone's note, and an item with no content is one this
        # credential cannot see: neither is this verb's to judge.
        if item.kind not in ("ISSUE", "PULL_REQUEST") or not item.repo:
            continue
        if (item.repo not in repos or item.kind != "ISSUE"
                or item.issue_type != INITIATIVE):
            result.remove.append(item)
    targets = copied(config, fields)
    for initiative in initiatives:
        present = on_board.get(initiative.id)
        if present is None:
            result.add.append(initiative)
        current = present.values if present else {}
        for target in targets:
            want, was = text(initiative.values.get(key(target.name))), \
                text(current.get(key(target.name)))
            if want == was:
                continue
            if (want and target.data_type == "SINGLE_SELECT"
                    and want not in target.option_ids):
                result.skipped.append(
                    f"{initiative.ref} {target.name} {want!r}: the portfolio "
                    f"has no such option")
                continue
            result.set.append((initiative, target, want, was))
    return result


def send(parts: list[str]) -> dict:
    """Mutations in batches of aliased fields; returns what each alias gave."""
    answers: dict = {}
    for start in range(0, len(parts), BATCH):
        document = "mutation { " + " ".join(parts[start:start + BATCH]) + " }"
        payload = run_json(["gh", "api", "graphql", "-f", f"query={document}"])
        if payload.get("errors"):
            said = "; ".join(e.get("message", "") for e in payload["errors"])
            raise BoardError(f"GitHub refused part of the portfolio sync: {said}")
        answers.update(payload.get("data") or {})
    return answers


def apply(project_id: str, result: Plan, items: list[Item]) -> None:
    """Add, then copy, then remove: an added item needs its id before any
    value can be written to it."""
    ids = {item.content_id: item.id for item in items if item.content_id}
    if result.add:
        made = send([f"a{index}: addProjectV2ItemById(input: {{"
                     f" projectId: {quoted(project_id)},"
                     f" contentId: {quoted(initiative.id)} }}) {{ item {{ id }} }}"
                     for index, initiative in enumerate(result.add)])
        for index, initiative in enumerate(result.add):
            ids[initiative.id] = made[f"a{index}"]["item"]["id"]
    parts = []
    for index, (initiative, target, value, _was) in enumerate(result.set):
        where = (f"projectId: {quoted(project_id)}, itemId: {quoted(ids[initiative.id])},"
                 f" fieldId: {quoted(target.id)}")
        if not value:
            parts.append(f"s{index}: clearProjectV2ItemFieldValue(input: {{ {where} }})"
                         " { projectV2Item { id } }")
            continue
        literal = value_literal(Write(ref=initiative.ref, field=target.name,
                                      value=value), target)
        parts.append(f"s{index}: updateProjectV2ItemFieldValue(input: {{ {where},"
                     f" value: {literal} }}) {{ projectV2Item {{ id }} }}")
    send(parts)
    send([f"r{index}: deleteProjectV2Item(input: {{ projectId: {quoted(project_id)},"
          f" itemId: {quoted(item.id)} }}) {{ deletedItemId }}"
          for index, item in enumerate(result.remove)])


def sync(config: Config, dry: bool = False) -> int:
    """`board portfolio sync`: converge the portfolio, or say what would change."""
    if config.portfolio is None or not config.portfolio.repos:
        raise BoardError(
            f"this board is not a portfolio: list the repositories whose "
            f"Initiatives it shows, as [portfolio] repos = [...] in {config.where}")
    auth.require(config, "read" if dry else "write")
    number = locate(config)
    if not number:
        raise BoardError("the portfolio board does not exist yet; "
                         "`board admin apply --as-me` creates it")
    project_id, raw, items = fetch_portfolio(config, number)
    fields = parse_fields(raw, config)
    initiatives, unread = [], []
    for repo in config.portfolio.repos:
        try:
            initiatives += fetch_initiatives(repo, project_id)
        except BoardError as refused:
            # The others are still worth bringing up to date, and nothing is
            # removed for being unread: removal reads the issue, not a list.
            unread.append(str(refused))
    result = plan(config, fields, items, initiatives)
    verb = "would " if dry else ""
    for initiative in result.add:
        print(f"{verb}add {initiative.ref} {initiative.title}")
    for initiative, target, value, was in result.set:
        print(f"{verb}set {initiative.ref} {target.name} = {value or 'unset'}"
              f" (was {was or 'unset'})")
    for item in result.remove:
        why = ("not listed" if item.repo not in config.portfolio.repos
               else "not an Initiative")
        print(f"{verb}remove {item.repo.split('/')[-1]}#{item.number} ({why})")
    if not dry:
        apply(project_id, result, items)
        note("portfolio_synced", project=number, initiatives=len(initiatives),
             added=len(result.add), set=len(result.set), removed=len(result.remove))
    for line in [*result.skipped, *unread]:
        print(f"warning: {line}")
    if not (result.add or result.set or result.remove):
        print(f"{len(initiatives)} Initiative(s) from "
              f"{len(config.portfolio.repos)} repositories; nothing to change.")
    return 1 if unread else 0
