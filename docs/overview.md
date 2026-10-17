# Overview

Boardkit gives people and agents one repository-owned interface to a GitHub
Project. It can create the project, fields and saved views declared by the
repository, and it keeps a local working copy for fast and offline reads.

==================
## Creating a new board

Boardkit allow a simple way to create and configure git projects.

==================
## Working with the local cache

### Local Caching

`board sync` snapshots the remote project under `.boardkit/`.
Snapshots are time-stamped.

Snapshots are re-sync when:
    * a read of the board is requested
    * the snapshot last sync is that older a configurable window (default to 5 min)

A write does not re-sync the whole snapshot. It works on the cache and
refreshes only the cards it writes to, as below.

This limits drastically the number of calls to the gitprojects API and provide an soft upper bound on the amount of quotas used.

The reason for the snapshot window is if the gitproject boards is updated directly in the ui by a human or another process.

### Local Updates

The agents working locally will work on the cached snapshot.
If during their operations they write cards, the update is inserted in a queue of item to be synced.

By default we sync the write right away.
If we work offline, the queue can contain several card's updates.
Those will be sync at the next write or snapshot sync, whichever occurs first.
The write only need to fetch the cards that are updated, and the pull is batched for a item in the queues, minimizing the API calls as well.

If a conflict is detected, that update is rejected and kept with both values.
Only the conflicting field is affected : 39 of 40 writes in a batch still land, one is recorded.
The next write to a rejected field is refused once, naming what the board holds, so the conflict is seen before it can be overwritten. Repeating the same command sends it anyway, which stops a blind overwrite and not a determined one.

### Eventual consistency

The end goal is to minimize latency and quota spent, allow the agents to work if if the board is down.
The first sync is the exception : with no snapshot at all a read says to run `board sync` rather than inventing one.

Retries : if despite those efforts, the quota is reached, the sync will fail and will be attempted again by the next read once the window has elapsed.
Nothing polls and nothing is supervised : the retry rides on a command someone ran anyway.

GitHub remains the source of truth. The local store is a disposable working
copy, while queued and rejected writes preserve decisions that have not landed.

=============================

## Repository configuration

Each consuming repository commits `boardkit.toml`, or a `[tool.boardkit]`
table in `pyproject.toml`. That file identifies the project and may declare the
fields and views `board admin apply` should create. Runtime state and credentials
never belong in this committed configuration.

Any setting that holds one value is also readable as `BOARDKIT_<SETTING>`, which
wins over the file. That is how a default is changed for one run or one machine
without editing something that is committed.
The list settings are not : fields, views and status_order describe the board, not the run.

==============================
## Agent integration

Agents find skills in the repository they work in or in their user
directory. boardkit uses the user directory only: the Claude Code plugin
carries the skills beside the `board` they describe, and `install.sh` writes
the same files where Codex reads user skills. boardkit runs on a person's own
machine, so per-user is the whole audience, and copies committed in each
repository only drifted from the command installed beside them.

`board install-skill` renders the plugin's copies in boardkit itself; in any
other repository it names copies an earlier boardkit committed, to delete.
`board doctor` reports them too.
