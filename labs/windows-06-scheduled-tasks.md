# Windows 06 - Scheduled PowerShell tasks

## Objective

Create, run, monitor and troubleshoot scheduled tasks that execute PowerShell scripts unattended, choosing the correct principal, trigger, execution policy handling and settings, and using Task Scheduler history to prove what happened.

## Scenario

> **Ticket REQ-2290** - Priority 3
> Operations wants a disk-space report on `asrs-lab-windows` every 15 minutes, written to `C:\Ops\Logs\disk-report.log`, running whether or not anyone is logged on, and surviving reboots. A second task must run the same script once at startup. Last quarter a similar task "ran successfully" for weeks while producing nothing because the script path was wrong - so this time you must be able to prove from history and exit codes whether each run actually worked. Also write a short note on which account the task should run as and why.

## Prerequisites

- SSM PowerShell session on Windows.
- Optional: RDP access (exercise 01) to view Task Scheduler (`taskschd.msc`) graphically.
- Optional: a dedicated local service account for the task (create it as part of the task if you choose that design).

### Set up a fault for task 8 (you do this yourself, after tasks 1-7 work)

Register a copy of your task that points at a script path that does not exist, for example `C:\Ops\Scripts\Get-DiskReprot.ps1` (note the typo), and a second copy whose script ends with `exit 3`. Run both.

## What you need to accomplish

1. Create `C:\Ops\Scripts\Get-DiskReport.ps1` that appends a timestamped line per fixed volume (drive letter, size GB, free GB, free %) to `C:\Ops\Logs\disk-report.log`, and exits non-zero if any volume is below 15% free.
2. Check the effective PowerShell execution policy on the server and decide how the task will run the script without weakening the machine-wide policy.
3. Enable Task Scheduler history (it is off by default on Server 2022).
4. Register task `\Ops\DiskReport` that runs the script every 15 minutes indefinitely, starting now, whether a user is logged on or not, with highest privileges, using a principal that does not depend on a person's password. Justify the principal.
5. Configure sensible settings: no overlapping instances, an execution time limit, start when available after a missed run, and run on battery is irrelevant but know where it is.
6. Register `\Ops\DiskReport-Startup` that runs the same script at system startup.
7. Run the task on demand and prove it worked: log file content, last run time, last result code, and history events.
8. Inject the fault (wrong path and `exit 3`) and show how each failure looks in `LastTaskResult` and in the event history. Explain why the wrong-path task can still show "The operation completed successfully" in some views.
9. Export the working task definition to XML for source control.
10. Reboot and prove the startup task ran.

## Commands or tools to research

- `New-ScheduledTaskAction`, `New-ScheduledTaskTrigger` (`-Once -RepetitionInterval`, `-AtStartup`, `-Daily`), `New-ScheduledTaskPrincipal` (`-UserId`, `-LogonType`, `-RunLevel`), `New-ScheduledTaskSettingsSet`
- `Register-ScheduledTask`, `Set-ScheduledTask`, `Start-ScheduledTask`, `Get-ScheduledTask`, `Get-ScheduledTaskInfo`, `Export-ScheduledTask`, `Unregister-ScheduledTask`
- `schtasks.exe /query /v /fo list`, `/run`
- `Get-ExecutionPolicy -List`, `powershell.exe -ExecutionPolicy Bypass -NoProfile -File`, `Unblock-File`
- `wevtutil set-log`, log `Microsoft-Windows-TaskScheduler/Operational`, event IDs 100, 101, 102, 106, 110, 111, 129, 140, 141, 200, 201, 203, 322
- `Get-Volume`, `Get-CimInstance Win32_LogicalDisk`
- "Log on as a batch job" user right (`secpol.msc`), Group Managed Service Accounts (concept)

## Validation criteria

- `Get-ScheduledTask -TaskPath '\Ops\'` lists both tasks `Ready`; principal shown as `SYSTEM` (or your service account) with `RunLevel Highest`.
- `C:\Ops\Logs\disk-report.log` gains new lines every 15 minutes without anyone connected.
- `Get-ScheduledTaskInfo` shows `LastTaskResult 0` for the good task, a non-zero value for each faulty one, and `NextRunTime` about 15 minutes ahead.
- The TaskScheduler Operational log contains 100/200/201/102 for a successful run, and 201 with a non-zero return code for the failed ones.
- An XML export of `\Ops\DiskReport` exists.
- After a reboot, the startup task shows a `LastRunTime` matching the boot time.

## Recovery considerations

- A task running as SYSTEM can do anything on the box; keep the script in a folder only administrators can write to (`C:\Ops\Scripts`), otherwise a non-admin could edit the script and escalate.
- If you register with a user account and password, the task breaks when that password changes (event 101 / result `0x8007052E` logon failure). Removing that user's batch-logon right breaks it too.
- Do not set the machine execution policy to `Unrestricted` to make a task work; use `-ExecutionPolicy Bypass` on the task's command line only.
- A runaway task (tight loop, every minute) on a t3.small can burn CPU credits; disable it with `Disable-ScheduledTask` or delete it. If the box becomes unresponsive, stop/start the instance and disable the task from the SSM session.
- Export tasks to XML before modifying; re-register from XML to roll back.

## Interview angle

"A scheduled task shows 'Ready' and the last result looks fine, but the job did nothing - how do you troubleshoot?" - Check `LastTaskResult` (0x1 / non-zero exit codes), the TaskScheduler Operational history (enable it), the action's working directory and script path, the principal and logon type, execution policy, and have the script log and exit with meaningful codes.

[Solution](solutions/windows-06-scheduled-tasks.md) — try it first.
