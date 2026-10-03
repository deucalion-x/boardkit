# Objective

* creates git projects 
* manage local cache of git projects for offline use

Agents can use boardkit to manage tasks.
Reads are local so no network hits on read, no api quotas used.


## Creating a new git project

* boardkit creates git projects
* boardkit creates customizable work items
* boardkit creates customizable views

## Local cache

## How syncing local and github projects board works

* Reads are local so no network hits on read, no api quotas used.
* Writes will happen immediately on changes
  * First pull remote state
  * If no conflict, push changes
  * Otherwise, record the conflict
    * when conflict is resolve, push can occur

## Offline use

* At each write we tried to push to the remote
  * if offline
    * write items are collected
      * successive writes on the same item are not creating several updates but merge right away
  * if online
    * proceed to syncing


## agent use of this tool

* the tool is a package othe project needing it will import as a package. 
* we need in addition to install a skill that expose the api to a agent.
  * a package alone cannot do it: Claude Code only loads skills from
    `.claude/skills/` in the repo it works in, so a skill inside the installed
    package is never found
  * so the package ships the skill as a template and `board install-skill`
    renders it into the consuming repo, where it is committed
  * rendering fills in that repo's board url, columns, fields and grouping,
    from its config and its last sync
  * the rendered copy is generated, never hand-edited: change the template or
    the repo's `skill_notes` and run `board install-skill` again
  * `board install-skill --check` fails when the committed copy has drifted
