# Overview

Boardkit gives people and agents one repository-owned interface to a GitHub
Project. It can create the project, fields and saved views declared by the
repository, and it keeps a local working copy for fast and offline reads.

## Local-first operation

`board sync` snapshots the remote project under `.boardkit/`. Read commands
use that snapshot and therefore make no network request and spend no GitHub API
quota.

A field change is written to the durable local queue before boardkit attempts
to send it. Before sending, boardkit refreshes the target item and refuses to
overwrite a field that changed remotely. A successful write updates the local
snapshot. An offline or rate-limited write stays queued for `board sync push`;
a conflicting write moves to the rejection log with both values preserved.

GitHub remains the source of truth. The local store is a disposable working
copy, while queued and rejected writes preserve decisions that have not landed.

## Repository configuration

Each consuming repository commits `boardkit.toml`, or a `[tool.boardkit]`
table in `pyproject.toml`. That file identifies the project and may declare the
fields and views `board admin apply` should create. Runtime state and credentials
never belong in this committed configuration.

## Agent integration

An installed Python package cannot expose a repository skill by itself: agent
skills are discovered inside the repository where the agent is working.
Boardkit therefore ships canonical skill templates and `board install-skill`
copies them into `.claude/skills/` in the consuming repository.

Commit the generated skills so every contributor gets the same interface.
Do not edit generated copies; change the templates in boardkit and regenerate
them. `board install-skill --check` detects drift.
