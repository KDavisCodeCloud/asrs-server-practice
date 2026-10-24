# Windows 07 - Investigate errors in Event Viewer

## Objective

Query the Windows event logs efficiently from PowerShell to answer operational questions - unexpected reboots, service failures, application crashes and failed logons - and export evidence for a ticket.

## Scenario

> **Ticket INC-3240** - Priority 2
> Overnight, `asrs-lab-windows` "went away for a few minutes", a line-of-business app reportedly crashed, a service would not start, and the security team saw a burst of failed logons. The on-call engineer only has Session Manager access. Build a timeline from the event logs: when did the server go down, was it a clean or dirty shutdown, what crashed, which service failed and why, and which account names and logon types were used in the failed logons. Export the relevant events for the post-incident review.

## Prerequisites

- SSM PowerShell session on Windows. RDP (exercise 01) is optional for comparing with the Event Viewer GUI (`eventvwr.msc`) and Custom Views.
- AWS CLI on your workstation for the optional dirty-shutdown fault.
- Take a root-volume snapshot before the dirty-shutdown fault.

### Set up the faults (you do this yourself, in this order, noting the time of each)

1. **Failed logons** - from the SSM session, attempt SMB authentication to the server itself with a non-existent user and with a real user and a wrong password a few times:
   ```powershell
   1..3 | ForEach-Object { net use \\localhost\C$ /user:nosuchuser Wr0ngPass! 2>$null }
   1..2 | ForEach-Object { net use \\localhost\C$ /user:Administrator Wr0ngPass! 2>$null }
   ```
2. **Service start failure** - create a deliberately broken service and try to start it:
   ```powershell
   New-Service -Name LabBrokenSvc -BinaryPathName 'C:\Lab\missing.exe' -StartupType Manual | Out-Null
   Start-Service LabBrokenSvc
   ```
3. **Application crash** - crash a child PowerShell process:
   ```powershell
   powershell.exe -NoProfile -Command "[Environment]::FailFast('INC-3240 lab crash')"
   ```
4. **Clean restart** - `Restart-Computer -Force`, reconnect after ~3 minutes.
5. **Dirty shutdown (optional, snapshot first)** - from your workstation trigger a diagnostic interrupt, which makes Windows bug-check and reboot:
   ```bash
   aws ec2 send-diagnostic-interrupt --instance-id <windows-instance-id>
   ```
   Wait 5 minutes, then reconnect via SSM.

## What you need to accomplish

1. List the classic logs and their sizes/record counts; find which logs exist for a provider such as Task Scheduler or Windows Update.
2. Pull all Critical and Error events from the System log for the last 24 hours, newest first, grouped by provider and event ID with counts.
3. Build a reboot timeline: clean shutdowns, OS start/stop, and unexpected shutdowns. Distinguish the clean restart (fault 4) from the dirty one (fault 5) using event IDs only.
4. Identify the service that failed to start and the reason (fault 2).
5. Identify the crashed application, its exception code and faulting module (fault 3).
6. From the Security log, list failed logons with time, target account, logon type, failure status/sub-status, and source process/address. Explain what the sub-status codes mean.
7. Confirm the audit policy that makes those Security events appear.
8. Export the relevant events to `.evtx` and to CSV for the post-incident review.
9. Write the timeline (5-10 lines) in the ticket.

## Commands or tools to research

- `Get-WinEvent` (`-ListLog`, `-ListProvider`, `-FilterHashtable`, `-FilterXPath`, `-MaxEvents`), event `Properties` and `ToXml()`
- `Get-EventLog` (legacy, Windows PowerShell only) and why `Get-WinEvent` is preferred
- `wevtutil qe`, `wevtutil epl`, `wevtutil gli`
- `Group-Object`, `Export-Csv`
- `auditpol /get`
- Event IDs: System 41, 1074, 1076, 6005, 6006, 6008, 6013, 1001 (BugCheck), 7000, 7001, 7009, 7023, 7031, 7034, 7036; Application 1000, 1001, 1026; Security 4624, 4625, 4740
- NTSTATUS codes `0xC000006D`, `0xC000006A`, `0xC0000064`, `0xC0000072`, `0xC0000234`
- `aws ec2 send-diagnostic-interrupt`, EC2 "Get system log" / "Get instance screenshot"

## Validation criteria

- A command returns System Critical/Error counts grouped by provider and ID, including Service Control Manager 7000 for `LabBrokenSvc`.
- Your reboot timeline shows 1074 + 6006 + 6005 for the clean restart and, if you did fault 5, 41 (Kernel-Power) + 6008 + 1001 (BugCheck) without a preceding 1074.
- You can show Application Error 1000 (or .NET Runtime 1025/1026) naming `powershell.exe`.
- You list at least five 4625 events with logon type 3, distinguishing unknown user (`0xC0000064`) from bad password (`0xC000006A`).
- An `.evtx` and a `.csv` file exist with only the filtered events.

## Recovery considerations

- `send-diagnostic-interrupt` causes a real crash and an unclean shutdown - snapshot first; it is safe on this lab instance but never do it to production. If the instance does not return to Online in Fleet Manager within ~10 minutes, check status checks and the instance screenshot, then stop/start.
- Do not clear logs as part of the investigation (`Clear-EventLog`/`wevtutil cl`) - that destroys evidence and itself writes event 104/1102.
- Failed-logon testing against `Administrator` can trip an account lockout policy if one is configured (`net accounts`); the default on a standalone server has no lockout threshold, but check. If an account locks, unlock it from the SSM session (`Unlock-LocalUser` does not exist - use `net user <name> /active:yes` or wait for the lockout duration).
- Large `Get-WinEvent` queries without filters on a 2 GiB instance are slow and memory-hungry; always filter at the source with `FilterHashtable`/`FilterXPath`, not with `Where-Object`.

## Interview angle

"The server rebooted overnight - was it planned?" - 1074 (who/what initiated, reason code) plus 6006/6005 = planned/clean; 41 + 6008 with no 1074 = unexpected (power loss, hang, bug-check - then look for 1001 BugCheck and the dump). Correlate with AWS: instance status checks, scheduled events and CloudTrail (StopInstances/RebootInstances).

[Solution](solutions/windows-07-event-viewer.md) — try it first.
