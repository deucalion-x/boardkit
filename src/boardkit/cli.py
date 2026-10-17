"""One entry point, so every verb shares the same configuration lookup.

Reads answer from the local store; only the sync verbs touch GitHub.

  board                      every open item, by status
  board item <n>             one item with its text and children
  board find <terms>         search titles, bodies and labels in the snapshot
  board json                 normalized items on stdout, for jq or python
  board views                the saved tabs the board has
  board report [--json]      where each Initiative stands: done/total, dates,
                             the active ones with what is blocked, what is late

  board add <title> [f=v]    create an issue and put it on the board
                             --body <text> for its text
                             --draft for a board card with no issue behind it
  board promote <ref>        turn a draft card into an issue, keeping its fields
  board comment <ref> <text> record a result or decision on the issue
  board close <ref> [--not-planned]   close it; not planned for dropped work
  board reopen <ref>
  board edit <ref> [--title t] [--body b]
  board set <ref> labels|assignees "a, b"   or parent <n> (sub-issue)
                             or milestone "<title>" (its phase; "" removes)
                             or type <name> (Initiative, Feature, Task, Bug)
  board set <n> <field> <v>  change a field; pushed now, queued if it cannot be
                             --later: queue without trying
                             --anyway: move a draft out of the intake column
  board rejected [--clear]   local changes that lost to a remote change
  board log [n] [--superuser]  what boardkit did; --superuser: uses of your
                             own login (--as-me, approved gh)
  board log --usage [days]   API calls and points spent, reads answered locally
  board sync [push|status]   pull, drain the queue, or report
  --offline                  on any read: answer from the snapshot as it is
  board auth [--refresh]     which credential is used, and what it may do
  board auth app create      create this repo's own GitHub App, in one go
  board auth login|logout    sign in through this repo's GitHub App
  board auth setup           or store a token for this repo alone
  --as-me                    on any verb: use your own gh login instead of
                             this repo's credential; approved and logged
  board init [-n] [--preset pm] [--app <client-id>] [title]
                             write the three settings a repo must decide;
                             --preset starts the board from a shipped shape;
                             --app names the organisation's shared app
  board schema               the configuration's shape, as JSON schema
  board admin apply [-n] [--reconcile] [--prune-views] [--preset pm]
                             add missing shape; optionally update view filters
  board admin checklist      what the preset leaves to set in the browser
  board admin link           attach the project to the repository
  board portfolio sync [-n]  on a portfolio: add the listed repositories'
                             Initiatives and copy their fields; the only
                             thing that changes a portfolio

  board mirror write|check   the issue-text mirror in git
  board links check|rewrite  sha-pinned document links in issue bodies
  board install-skill        in boardkit: render the plugin's skills; elsewhere:
                             name skill copies a repository should delete
  board doctor               which `board` runs and which skills agents read
  board self update [v]      reinstall a release made by install.sh
  board --version            which boardkit is installed
"""
from __future__ import annotations

import json
import os
import sys

from . import (
    admin,
    auth,
    create,
    credential,
    doctor,
    issue,
    issue_mirror,
    login,
    plan_links,
    portfolio,
    quota,
    report,
    setup,
    skill,
    sync,
    trail,
)
from . import board as board_module
from .config import load
from .gh import BoardError, RateLimited, Unreachable
from .store import Snapshot, Store, Write

BOARD_COMMANDS = {"list", "item", "find", "json", "views", "report"}
# What changes an item. A portfolio's items are copies, overwritten by the
# next `board portfolio sync`, so these are refused there.
WRITES = {"add", "promote", "set", "comment", "close", "reopen", "edit"}
STALE = "No local snapshot. Run `board sync` first."


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Any verb may ask for the personal gh login instead of the repo's credential.
    as_me = "--as-me" in argv
    argv = [a for a in argv if a != "--as-me"]
    command = argv[0] if argv else "list"
    try:
        return dispatch(command, argv[1:], as_me=as_me)
    except BoardError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        # `board json | head` closed the pipe; that reader got what it wanted.
        # Point stdout at nothing so the interpreter's own flush at exit does
        # not raise a second time.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 141


HELP = {"-h", "--help", "help"}


def dispatch(command: str, args: list[str], as_me: bool = False) -> int:
    # Help needs no configuration, so it works outside a configured repo.
    if command in HELP:
        return usage(0)
    # Likewise the version, which is how a person checks what a tag installed.
    if command in ("--version", "version"):
        from . import __version__
        print(f"boardkit {__version__}")
        return 0
    # A verb asked for help must not run: `install-skill --help` used to write
    # the skills. The value after `--body` is text, so it is not a flag.
    flags = [a for i, a in enumerate(args) if i == 0 or args[i - 1] != "--body"]
    if any(a in HELP for a in flags):
        return usage(0)

    # About the installation, not a board: they run in any directory.
    if command == "doctor":
        try:
            repo = load().root
        except BoardError:
            repo = None
        return doctor.report(repo)
    if command == "self":
        if args[:1] != ["update"]:
            return usage()
        return doctor.update(args[1:])

    # These two run before configuration exists, which is their whole point.
    if command == "init":
        preset, args = option(args, "--preset")
        client_id, args = option(args, "--app")
        return setup.init(" ".join(a for a in args if a != "-n"), dry="-n" in args,
                          preset=preset, app=client_id)
    if command == "schema":
        return setup.schema()

    preset, args = option(args, "--preset") if command == "admin" else ("", args)
    config = load(preset=preset)
    doctor.nudge(config.root)
    credential.configure(config.repo, elevated=as_me, app_id=config.app_client_id)
    store = Store(config)
    quota.begin(command)
    # Named once per command, because three files can hold this configuration
    # and the costly confusion is editing one that is not the one being read.
    trail.note("configured", file=str(config.source), command=command,
               project=config.name, declares=config.declares)
    # The escape hatch is on record every time, not only approved: it runs
    # with the person's full login, beyond this repository's credential.
    if as_me:
        superuser(config, "--as-me", " ".join(["board", command, *args]))

    # Verbs that never read the board are handled before it is loaded.
    if command == "install-skill":
        return skill.install(config, check="--check" in args)
    if command == "admin":
        verb = args[0] if args else "apply"
        if verb == "apply":
            code = admin.apply(config, dry="-n" in args,
                               reconcile="--reconcile" in args,
                               prune="--prune-views" in args)
            if preset and load().preset != preset:
                # project.toml holds identity only, so a preset never goes there.
                where = (f"a boardkit.toml in {config.root}"
                         if config.source == config.identity_source else config.where)
                print(f'\nTo keep it, add preset = "{preset}" under [board] in '
                      f"{where}; this run applied it once.")
            return code
        if verb == "link":
            return admin.link(config)
        if verb == "checklist":
            return admin.checklist(config)
        return usage()
    if command == "portfolio":
        if args[:1] != ["sync"]:
            return usage()
        return portfolio.sync(config, dry="-n" in args)
    if config.portfolio is not None and command in WRITES:
        raise BoardError(
            "this board is a portfolio: each item is a copy of an Initiative "
            "on its own repository's board. Change it there; `board portfolio "
            "sync` brings the copy up to date.")
    if command == "auth":
        if args[:1] == ["setup"]:
            return auth.setup(config)
        if args[:2] == ["app", "create"]:
            from . import app
            return app.create(config)
        if args[:1] == ["login"]:
            return login.login(config.app_client_id, config.repo)
        if args[:1] == ["logout"]:
            return login.logout(config.app_client_id, config.repo)
        return auth.report(config, refresh="--refresh" in args)
    if command == "sync":
        verbs = [a for a in args if not a.startswith("-")]
        return run_sync(config, store, verbs[0] if verbs else "pull")
    if command == "add":
        title, body, values, draft = create.parse(args)
        if draft:
            return create.add_draft(config, store, title, body, values)
        return create.add(config, store, title, body, values)
    if command == "promote":
        if not args:
            return usage()
        # Gated here rather than inside: creating the issue is a repository
        # write, and a missing scope should read as advice rather than as
        # whatever GitHub says after the card is half-converted.
        auth.require(config, "write")
        return create.promote(config, store, args[0].lstrip("#"))
    if command == "set":
        words = [a for a in args if a not in ("--later", "--anyway")]
        if len(words) >= 2 and words[1] in issue.META:
            return issue.set_meta(config, store, words[0], words[1], " ".join(words[2:]))
        return queue(config, store, args)
    if command == "comment" and len(args) >= 2:
        return issue.comment(config, store, args[0], " ".join(args[1:]))
    if command == "close" and args:
        reason = "not-planned" if "--not-planned" in args else "completed"
        return issue.close(config, store, args[0], reason)
    if command == "reopen" and args:
        return issue.reopen(config, store, args[0])
    if command == "edit" and args:
        rest = list(args[1:])
        changes: dict[str, str] = {}
        while rest:
            word = rest.pop(0)
            if word in ("--title", "--body") and rest:
                changes[word[2:]] = rest.pop(0)
        return issue.edit(config, store, args[0], changes.get("title"), changes.get("body"))
    if command == "log":
        if "--record-gh" in args:
            # Fed by a Claude Code hook with each approved raw `gh` command.
            said = sys.stdin.read().strip()
            if said.startswith("gh ") or said == "gh":
                superuser(config, "gh", said)
            return 0
        if "--usage" in args:
            return quota.report(next((int(a) for a in args if a.isdigit()), 7))
        limit = next((int(a) for a in args if a.isdigit()), 30)
        return history(store, limit, superuser_only="--superuser" in args)
    if command == "rejected":
        return rejected(store, clear="--clear" in args)
    if command == "mirror":
        verb = args[0] if args else "check"
        if verb == "write":
            return issue_mirror.write(config)
        if verb == "check":
            return issue_mirror.check(config)
        return usage()
    if command == "links":
        verb = args[0] if args else "check"
        if verb == "check":
            return plan_links.check(config)
        if verb == "rewrite":
            return plan_links.rewrite(config, dry="-n" in args)
        return usage()
    if command not in BOARD_COMMANDS:
        return usage()
    if command == "item" and not [a for a in args if a != "--offline"]:
        return usage()

    snapshot = freshen(config, store, offline="--offline" in args)
    # Stripped after the freshness decision so `find` does not search for it
    # and `item` does not take it for a reference.
    args = [a for a in args if a != "--offline"]
    if snapshot.empty:
        print(STALE)
        return 1
    items = list(snapshot.items)
    # Reads about one item, or a search, also see closed work; the listing
    # and everything that writes see open work only.
    everything = [*items, *snapshot.closed, *snapshot.elsewhere]
    index = {item.ref: item for item in everything}
    # How to read this board: config where it asserts, the snapshot otherwise.
    view = board_module.shape(config, snapshot.fields)
    # Which board this is, said rather than assumed. An answer about work gets
    # read by a person who may then want to look at it, and nothing else in
    # the output names the board — a rule in the skill would need the agent to
    # notice the first time, which is the thing that does not work.
    #
    # Not on `json`: that output feeds jq, where a header is a parse error.
    if command == "report":
        built = report.build(everything, view, config.blocked_field,
                             board=snapshot.project_url)
        print(report.as_json(built) if "--json" in args else
              "\n".join(filter(None, [snapshot.project_url, report.render(built)])))
        return 0
    if command != "json" and snapshot.project_url:
        print(snapshot.project_url)
    if command == "views":
        for name in snapshot.views:
            print(name)
        if not snapshot.views:
            print("No views; `board sync` has not seen any.")
    elif command == "list":
        board_module.show(items, view)
        report_queue(store)
    elif command == "item":
        ref = args[0].lstrip("#")
        found = index.get(ref)
        if found is None:
            # The snapshot holds every issue of the repository, so absent
            # means it does not exist here — deleted, or never made.
            print(f"#{ref} does not exist in {config.repo}: no issue or card "
                  f"by that reference.")
            return 1
        board_module.show([found], view)
        if found is not None:
            about = [f"pull requests: {', '.join(found.linked_prs)}" if found.linked_prs else "",
                     f"review: {found.review}" if found.review else "",
                     f"mergeable: {found.mergeable}" if found.mergeable else "",
                     (f"checks: {found.checks or 'unknown'}"
                      if found.type == "PULL_REQUEST" else ""),
                     f"labels: {', '.join(found.labels)}" if found.labels else "",
                     f"assignees: {', '.join(found.assignees)}" if found.assignees else "",
                     f"milestone: {found.milestone}" if found.milestone else ""]
            said = "  ".join(part for part in about if part)
            if said:
                print(f"  {said}")
        if found is not None and found.closed:
            why = found.state_reason.replace("_", " ").lower()
            print(f"  closed{' as ' + why if why else ''}"
                  f"{', ' + found.closed_at[:10] if found.closed_at else ''}")
        if found is not None and not found.on_board:
            print("  not on the board")
        if found is not None and not found.body:
            # Silence read as "the skill lied about showing the full text".
            print("  (no description)")
        if found is not None and found.body:
            print()
            print(found.body.rstrip())
            print()
        if found is not None and found.comments:
            left_out = found.comment_count - len(found.comments)
            print(f"  comments ({found.comment_count}"
                  f"{f', the earliest {left_out} not kept' if left_out else ''}):")
            for comment in found.comments:
                print(f"\n  {comment.author}, {comment.at[:10]}:")
                print("    " + comment.body.rstrip().replace("\n", "\n    "))
            print()
        # Local, like the rest of the answer: each item carries its parent, so
        # the children are a pass over the snapshot. This was a search per
        # invocation — the one read that always spent quota, and the one that
        # could fail for a reason the question had nothing to do with.
        kids = board_module.children(found.number, items) if found else []
        if kids:
            print("  children:")
            board_module.show(kids, view)
    elif command == "find":
        # The snapshot carries titles, bodies and labels, open or closed, so
        # this costs nothing, including "was this already done".
        hits = board_module.matching(everything, " ".join(args))
        live = [h for h in hits if not h.closed and h.on_board]
        done = [h for h in hits if h.closed]
        off = [h for h in hits if not h.closed and not h.on_board]
        if live or not hits:
            board_module.show(live, view)
        if off:
            print("open, not on the board:")
            board_module.show(off, view)
        if done:
            print("closed:")
            board_module.show(done, view)
    elif command == "json":
        json.dump([{**item.flat(), "closed": item.closed, "on_board": item.on_board,
                    "state_reason": item.state_reason, "type": item.type,
                    "linked_prs": list(item.linked_prs), "review": item.review,
                    "mergeable": item.mergeable, "checks": item.checks}
                   for item in everything],
                  sys.stdout, indent=2)
        print()
    return 0


def option(args: list[str], flag: str) -> tuple[str, list[str]]:
    """The value after `flag`, and the arguments without either."""
    if flag not in args:
        return "", args
    at = args.index(flag)
    if at + 1 >= len(args):
        raise BoardError(f"{flag} needs a value")
    return args[at + 1], args[:at] + args[at + 2:]


def freshen(config, store: Store, offline: bool = False) -> Snapshot:
    """The snapshot a read may answer from, pulled first if it is too old.

    Being told to sync does not work: the agent has no way to notice that the
    instruction applies, and a stale answer looks exactly like a current one.
    So the read spends the pull itself, and the skill can promise an age
    instead of asking anyone to remember.

    Nothing is supervised and nothing polls. A pull that cannot reach GitHub
    leaves the snapshot alone and says so, because answering from a known-old
    copy beats failing the question.

    The window elapsing is also the only regular tick there is, so a queue
    left behind by an offline write is retried here before the pull.
    """
    snapshot = store.state()
    floor = config.max_read_age_seconds
    # No snapshot is not staleness. A first read says to sync rather than
    # fetching one behind the caller's back, so that setting a repo up is
    # something they did and not something that happened to them.
    if snapshot.empty:
        return showing(store, snapshot)
    if offline or not floor or snapshot.age <= floor:
        quota.read("local")
        return showing(store, snapshot)
    try:
        auth.require(config, "read")
        drain(config, store)
        pulled = sync.pull(config, store)
        quota.read("refreshed")
        return showing(store, pulled)
    except (BoardError, RateLimited, Unreachable) as why:
        quota.read("stale")
        print(f"note: answering from a snapshot {int(snapshot.age)}s old; "
              f"could not refresh it ({why})", file=sys.stderr)
        return showing(store, snapshot)


def showing(store: Store, snapshot: Snapshot) -> Snapshot:
    """The snapshot as a read should see it, with queued writes folded in.

    A write that has not landed is still a decision this repository has made.
    Leaving it out of reads means an agent sets a field, reads it back and is
    told the old value — worst offline, which is exactly when the queue is
    doing the job it exists for. The fold is the same operation a landed push
    performs, applied to the copy a read answers from.

    Not saved, on purpose. The file on disk goes on meaning "what GitHub last
    held", which is what keeps the conflict check honest and what lets a pull
    replace it without consulting anyone.

    Said out loud rather than shown silently: a value that is not on the board
    yet must not be indistinguishable from one that is.
    """
    pending = store.pending()
    if not pending:
        return snapshot
    print(f"note: {len(pending)} queued change(s) included below and not yet "
          f"on the board.", file=sys.stderr)
    return sync.patched(snapshot, pending)


def drain(config, store: Store) -> None:
    """Retry whatever the queue is still holding, before a refresh lands.

    A write that could not reach GitHub when it was made waited for someone
    to run `board sync push` — which, in an agent's session, means waiting for
    a person who is not there. The refresh is the retry: the window elapsing
    is the only regular tick boardkit has, it is already spending a call, and
    nothing has to be supervised for it to come around.

    Before the pull rather than after, because the pull replaces the snapshot
    these writes were decided against. Pushing first means a landed change is
    in what the pull returns; pushing after would compare them to values that
    had already moved.

    Best effort throughout. Still being offline is the ordinary case, and a
    read that asked for a refresh must not fail over the queue it inherited.
    """
    if not store.pending() and not store.actions():
        return
    try:
        auth.require(config, "write")
    except BoardError:
        return  # A read-only token keeps its reads. The queue keeps waiting.
    try:
        pushed, lost, _why = sync.push_now(config, store)
        sent, _ = issue.send(config, store)
    except BoardError:
        return
    if sent:
        print(f"note: sent {sent} queued issue change(s).", file=sys.stderr)
    # On stderr with the staleness note, and for the same reason: stdout is
    # the answer to the question that was asked, and this is not it.
    if pushed:
        print(f"note: pushed {pushed} queued change(s).", file=sys.stderr)
    if lost:
        print(f"note: {lost} queued change(s) rejected. "
              f"`board rejected` to see them.", file=sys.stderr)


def run_sync(config, store: Store, verb: str) -> int:
    if verb == "pull":
        # Gated before the call, so a missing scope reads as advice rather
        # than as whatever `gh` happens to print.
        auth.require(config, "read")
        drain(config, store)
        snapshot = sync.pull(config, store)
        print(f"Pulled {len(snapshot.items)} items and {len(snapshot.fields)} fields")
        return report_queue(store)
    if verb == "push":
        auth.require(config, "write")
        pushed, lost = sync.push(config, store)
        sent, why = issue.send(config, store)
        print(f"Pushed {pushed} change(s); {lost} rejected"
              + (f"; sent {sent} issue change(s)" if sent else "")
              + (f"; {len(store.actions())} issue change(s) kept, {why}" if why else ""))
        if sent:
            sync.pull(config, store)
        return 1 if lost else 0
    if verb == "status":
        snapshot = store.state()
        when = snapshot.synced_at or "never"
        print(f"Snapshot: {len(snapshot.items)} items, synced {when}")
        floor = config.max_read_age_seconds
        if not floor:
            print("Reads answer from it whatever its age "
                  "(max_read_age_seconds = 0)")
        elif not snapshot.empty:
            print(f"{int(snapshot.age)}s old; a read refreshes it past {floor}s")
        return report_queue(store)
    return usage()


def settling(config, snapshot, write: Write, asked: bool) -> None:
    """Refuse to walk a draft out of intake unless that is what was asked for.

    GitHub allows it and so does boardkit: the board is not ours to have
    opinions about, and a state a human made in the UI has to be readable and
    writable here. What is worth preventing is an agent doing it *by default* —
    which it did, because converting the card was impossible until `promote`
    existed, so moving it was the only action available.

    So this is a default and not a rule. `--anyway` does it, and the refusal
    names both that and the command the agent probably wanted.

    Repo-declared, not built in: the gate is the first column of
    `status_order`, which is the intake column by construction. A repo that
    declares no order gets no opinion, because boardkit would be asserting one
    workflow for every consumer of a general tool.
    """
    if asked or not config.status_order:
        return
    item = snapshot.item(write.ref)
    if item is None or not item.draft:
        return
    if write.field != config.status_field:
        return
    intake = config.status_order[0]
    if not write.value or write.value == intake:
        return
    raise BoardError(
        f"{write.ref} is a draft card, and drafts stay in `{intake}`. An idea "
        f"that has earned a plan becomes an issue: `board promote "
        f"{write.ref}` converts it in place, keeping its fields, and the "
        f"issue can then move. `--anyway` sets `{write.value}` on the draft "
        f"regardless.")


def queue(config, store: Store, args: list[str]) -> int:
    words = [a for a in args if a not in ("--later", "--anyway")]
    if len(words) < 3:
        return usage()
    ref, field, value = words[0].lstrip("#"), words[1], " ".join(words[2:])
    snapshot = store.state()
    if snapshot.empty:
        print(STALE)
        return 1
    if snapshot.item(ref) is None:
        found = snapshot.known(ref)
        if found is None or not found.number or found.closed:
            print(f"#{ref} is not on the board")
            return 1
        create.onto_board(config, store, found.number, found.url)
        snapshot = store.state()
        if snapshot.item(ref) is None:
            print(f"#{ref} was added; the board does not list it yet. "
                  f"Set it again in a moment.")
            return 1
    # Before recording, not after: a write refused here must never enter the
    # queue, where nothing would ever get it out again.
    settling(config, snapshot, Write(ref=ref, field=field, value=value),
             asked="--anyway" in args)
    sync.check(Write(ref=ref, field=field, value=value), snapshot)
    # After `check`, not before: a write that is invalid anyway should fail on
    # its value rather than spend the rejection that was guarding the field.
    sync.contested(Write(ref=ref, field=field, value=value), store)
    write = store.queue(ref, field, value)
    was = write.was or "unset"
    if "--later" in args:
        print(f"Queued #{ref} {field} = {value} (was {was}).")
        return report_queue(store)

    _pushed, lost, why = sync.push_now(config, store)
    if why:
        # No timer and no hook outliving the command: the next refresh past
        # the read-age window retries this (see `drain`), and `board sync
        # push` is what asking for it sooner looks like.
        print(f"#{ref} {field} = {value} (was {was}) — kept, {why}. "
              f"Retried on the next sync, or `board sync push` now.")
        return 0
    if lost:
        return rejected(store, clear=False)
    print(f"#{ref} {field} = {value} (was {was})")
    return 0


def rejected(store: Store, clear: bool) -> int:
    if clear:
        print(f"Cleared {store.clear_rejected()} rejection(s)")
        return 0
    rejections = store.rejected()
    if not rejections:
        print("No rejected changes.")
        return 0
    for rejection in rejections:
        write = rejection.write
        holds = f"GitHub holds {rejection.remote!r}; " if rejection.remote else ""
        print(f"#{write.ref} {write.field}: wanted {write.value!r} — "
              f"{holds}{rejection.reason}")
    print(f"\n{len(rejections)} rejected. Re-decide, then `board set` again; "
          f"`board rejected --clear` to drop them.")
    return 1


def superuser(config, via: str, command: str) -> None:
    """One use of the person's own login, recorded as such."""
    import getpass
    trail.note("superuser", via=via, repo=config.repo, user=getpass.getuser(),
               command=command[:500])


def history(store: Store, limit: int, superuser_only: bool = False) -> int:
    entries = store.history(limit if not superuser_only else 10_000)
    if superuser_only:
        entries = [e for e in entries if e.get("action") == "superuser"][-limit:]
    if not entries:
        print("Nothing recorded yet.")
        return 0
    for entry in entries:
        detail = "  ".join(f"{key}={value}" for key, value in entry.items()
                           if key not in {"at", "action"})
        print(f'{entry["at"]}  {entry["action"]:18} {detail}')
    return 0


def report_queue(store: Store) -> int:
    waiting, lost = len(store.pending()) + len(store.actions()), len(store.rejected())
    if waiting:
        print(f"{waiting} change(s) queued. `board sync push` to apply.")
    if lost:
        print(f"{lost} change(s) rejected. `board rejected` to see them.")
    return 0


def usage(code: int = 2) -> int:
    print(__doc__)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
