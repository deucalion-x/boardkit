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

