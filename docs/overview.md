# Objective

* creates git projects 
* manage local cache of git projects for offline use

Agents can use boardkit to manage tasks.
Reads are local so no network hits on read, no api quotas used.


## How syncing local and github projects board works

* Reads are local so no network hits on read, no api quotas used.
* Writes will happen immediately on changes
  * First pull remote state
  * If no conflict, push changes
  * Otherwise, record the conflict
    * when conflict is resolve, push can occur

