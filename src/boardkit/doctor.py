"""Which `board` runs, and which skills an agent reads beside it.

The plugin keeps the command and the skills at one version. Without it they
drift apart silently: an editable install left at an old release, skills
copied into a repository and never regenerated, a second `board` earlier on
PATH than the one the skills describe. Each was found by a person noticing an
answer was wrong. `board doctor` names them, and `board` itself mentions them
once a day, so an agent sees it too.
"""
from __future__ import annotations

import datetime
import os
import pathlib
import shutil
import subprocess
import sys

from . import skill


def home() -> pathlib.Path:
    return pathlib.Path(os.environ.get("HOME") or pathlib.Path.home())


def fallback_home() -> pathlib.Path:
    return pathlib.Path(os.environ.get("BOARDKIT_HOME")
                        or home() / ".local" / "share" / "boardkit")


def plugin_installed() -> bool:
    return (home() / ".claude" / "plugins" / "cache" / "boardkit").is_dir()


def user_roots() -> list[pathlib.Path]:
    """Where an agent finds skills for every repository. Codex's is
    $CODEX_HOME/skills; Claude's is only ours when the plugin is not."""
    roots = [pathlib.Path(os.environ.get("CODEX_HOME") or home() / ".codex") / "skills"]
    if not plugin_installed():
        roots.append(home() / ".claude" / "skills")
    return roots


def kind(path: pathlib.Path) -> str:
    text = str(path)
    if "/.claude/plugins/" in text:
        return "Claude Code plugin"
    try:
        ours = "# boardkit install.sh" in path.read_text(encoding="utf-8", errors="ignore")[:200]
    except OSError:
        ours = False
    if ours or text.startswith(str(fallback_home())):
        return "install.sh"
    if "/uv/tools/" in text:
        return "uv tool install"
    return "unknown origin"


def boards() -> list[tuple[pathlib.Path, pathlib.Path]]:
    """Every `board` on PATH, first one first: (as found, what it resolves to)."""
    found, seen = [], set()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = pathlib.Path(directory or ".") / "board"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            real = candidate.resolve()
            if real not in seen:
                seen.add(real)
                found.append((candidate, real))
    return found


def stale_skills(root: pathlib.Path) -> list[pathlib.Path]:
    """Our skills under a root that differ from what this boardkit writes.
    A skill that is absent is not stale: that root may simply not be used."""
    return [path for name in skill.SKILLS
            if (path := root / name / "SKILL.md").is_file()
            and path.read_text(encoding="utf-8") != skill.render(name)]


def repo_copies(repo: pathlib.Path) -> list[pathlib.Path]:
    """Our skills committed in a repository, which an earlier boardkit wrote."""
    marker = skill.GENERATED.splitlines()[0]
    return [path for root in skill.SKILL_ROOTS
            for path in sorted((repo / root).glob("*/SKILL.md"))
            if marker in path.read_text(encoding="utf-8", errors="ignore")]


def personal_login() -> bool:
    """Whether the person's own gh login can be read: by them, and by any
    agent running as them."""
    if not shutil.which("gh"):
        return False
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    try:
        found = subprocess.run(["gh", "auth", "token"], env=env, capture_output=True,
                               text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return found.returncode == 0 and bool(found.stdout.strip())


def findings(repo: pathlib.Path | None) -> list[str]:
    problems = []
    from . import coding
    if coding.stored() is not None and personal_login():
        problems.append("your own gh login is stored, so an agent can act as you "
                        "beyond the coding identity. Run `gh auth logout`, and sign in "
                        "only for an admin task; the README's \"Protecting the "
                        "personal login\" gives stronger ways.")
    found = boards()
    if not found:
        problems.append("no `board` on PATH. Install the plugin, or run install.sh "
                        "and put its bin directory on PATH.")
    elif len(found) > 1:
        listed = "; ".join(f"{link} ({kind(real)})" for link, real in found)
        problems.append(f"{len(found)} `board` commands on PATH, the first wins: {listed}. "
                        f"Remove all but one; a uv one goes with `uv tool uninstall boardkit`.")
    for root in user_roots():
        for path in stale_skills(root):
            problems.append(f"{path} was written by another boardkit than this one. "
                            f"Run `board self update`.")
    if repo is not None:
        for path in repo_copies(repo):
            problems.append(f"{path} is a skill copy kept in the repository; agents "
                            f"get it per user now. Delete it.")
    return problems


def report(repo: pathlib.Path | None) -> int:
    from . import __version__
    print(f"boardkit {__version__}, running from {pathlib.Path(sys.argv[0]).resolve()}")
    for link, real in boards():
        print(f"  board on PATH: {link} -> {real} ({kind(real)})")
    for root in user_roots():
        state = ("stale" if stale_skills(root) else "up to date") \
            if any((root / n / "SKILL.md").is_file() for n in skill.SKILLS) else "none"
        print(f"  user skills: {root}: {state}")
    if plugin_installed():
        print("  Claude Code: skills come from the plugin")
    from . import coding
    record = coding.stored()
    print(f"  coding identity: "
          f"{'signed in, app ' + record['client_id'] if record else 'none'}")
    print(f"  your own gh login: {'stored' if personal_login() else 'not stored'}")
    problems = findings(repo)
    for problem in problems:
        print(f"- {problem}")
    if not problems:
        print("Nothing to fix.")
    return 1 if problems else 0


def nudge(repo: pathlib.Path | None) -> None:
    """At most once a day, say on stderr what `board doctor` would. stderr,
    so `board json` stays clean; daily, so it is read and not tuned out."""
    if os.environ.get("BOARDKIT_NO_DOCTOR"):
        return
    cache = pathlib.Path(os.environ.get("XDG_CACHE_HOME") or home() / ".cache") / "boardkit"
    stamp = cache / "doctor-checked"
    today = datetime.datetime.now().astimezone().date().isoformat()
    try:
        if stamp.read_text(encoding="utf-8") == today:
            return
    except OSError:
        pass
    problems = findings(repo)
    try:
        cache.mkdir(parents=True, exist_ok=True)
        stamp.write_text(today, encoding="utf-8")
    except OSError:
        return
    for problem in problems:
        print(f"boardkit: {problem}", file=sys.stderr)


def update(args: list[str]) -> int:
    """Install a release again, command and skills together."""
    script = fallback_home() / "install.sh"
    if not script.is_file():
        if plugin_installed():
            print("This board comes from the Claude Code plugin, which updates itself: "
                  "`claude plugin update boardkit@boardkit`.")
        else:
            print("This board was not installed by install.sh, so there is nothing to "
                  "update from here. Run install.sh from the boardkit repository.")
        return 1
    return subprocess.run(["bash", str(script), *args[:1]], check=False).returncode
