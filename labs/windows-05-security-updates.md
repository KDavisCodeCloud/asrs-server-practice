# Windows 05 - Windows security updates

## Objective

Determine the patch level of a Windows Server 2022 instance, find and install missing security updates using built-in tooling, troubleshoot Windows Update from its logs, and explain how AWS Systems Manager Patch Manager does the same at fleet scale.

## Scenario

> **Ticket CHG-4502** - Monthly patching
> The security team's report says `asrs-lab-windows` is missing this month's cumulative update. Before the maintenance window: report the current build and the last installed updates, list what is missing, then install the security updates, reboot if required, and provide evidence (KB numbers, timestamps, final build) for the change record. If an update fails, find the error code in the logs. Bonus for the CAB: show how this would be done with Patch Manager instead of logging on to each server.

## Prerequisites

- SSM PowerShell session on Windows. Outbound 443/80 to the internet is allowed, so Windows Update (Microsoft Update service) is reachable directly.
- A root-volume snapshot (or AMI) taken before installing anything - this is part of the task.
- Time: on a t3.small (2 GiB RAM) a cumulative update scan takes 5-15 minutes and an install 30-90 minutes. `TiWorker.exe` will pin the CPU and draw down T3 CPU credits (see exercise 08). Do not reboot or stop the instance mid-install.
- AWS CLI on your workstation with permission to call `ec2 create-snapshot`, `ssm send-command` and `ssm describe-instance-patch-states` (optional part).

## What you need to accomplish

1. Report the OS build number (including the UBR/revision) and the installation date of the most recent updates.
2. Determine whether a reboot is already pending before you start.
3. Snapshot the root volume and record the snapshot ID in the ticket.
4. Scan for applicable, not-yet-installed software updates and list them with KB numbers and classification, using at least one built-in method.
5. Install the security/cumulative updates using a method of your choice (built-in, or the third-party `PSWindowsUpdate` module - if you use it, state that it is a community module from the PowerShell Gallery, not Microsoft-supported).
6. Determine whether a reboot is required; reboot in a controlled way and reconnect via SSM.
7. Produce evidence: new build/UBR, installed KBs with dates, and the Windows Update event log entries showing success.
8. Generate the readable `WindowsUpdate.log` and find the scan and install entries. Explain why there is no plain-text log by default.
9. Explain (and optionally run) the Patch Manager approach: scan-only first, then install, using the AWS-managed document. Show how to see the compliance result.

## Commands or tools to research

- `Get-ComputerInfo`, `[System.Environment]::OSVersion`, registry `HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion` (`CurrentBuild`, `UBR`, `DisplayVersion`)
- `Get-HotFix` (and its limits), `Get-CimInstance Win32_QuickFixEngineering`
- COM object `Microsoft.Update.Session` (`CreateUpdateSearcher`, `QueryHistory`)
- `UsoClient.exe` (`StartScan`, `StartDownload`, `StartInstall`), `sconfig` (option 6), Settings > Windows Update (via RDP)
- `PSWindowsUpdate` module: `Get-WindowsUpdate`, `Install-WindowsUpdate`, `Get-WUHistory`, `Invoke-WUJob`
- Pending reboot registry keys (`Component Based Servicing\RebootPending`, `WindowsUpdate\Auto Update\RebootRequired`)
- Services `wuauserv`, `UsoSvc`, `TrustedInstaller`, `BITS`
- `Get-WindowsUpdateLog`, `C:\Windows\Logs\WindowsUpdate\*.etl`, `C:\Windows\Logs\CBS\CBS.log`
- Provider `Microsoft-Windows-WindowsUpdateClient` events in the System log (IDs 19, 20, 43, 44) and the `Microsoft-Windows-WindowsUpdateClient/Operational` log
- `aws ec2 create-snapshot`, `aws ssm send-command --document-name AWS-RunPatchBaseline` (`Operation=Scan|Install`, `RebootOption`), `aws ssm describe-instance-patch-states`, `aws ssm describe-instance-patches`, patch baselines

## Validation criteria

- A snapshot of the root volume exists with `State = completed` and a description referencing CHG-4502.
- After patching, the UBR (build revision) is higher than before, and the scan for missing security updates returns nothing (or only items you deliberately deferred, with a reason).
- The System log shows `Microsoft-Windows-WindowsUpdateClient` event 19 (installation successful) for each installed KB.
- No reboot pending after the final reboot.
- `WindowsUpdate.log` exists and contains the scan/installation entries for your session.
- (Optional) `aws ssm describe-instance-patch-states` shows `MissingCount: 0` and `FailedCount: 0` for the instance after a Patch Manager scan.

## Recovery considerations

- Snapshot first. If the instance fails to boot after the update, create a volume from the snapshot and swap it in as the root volume (or use "Replace root volume" with the snapshot), then start the instance.
- A reboot ends your SSM session. Wait 3-10 minutes (updates finish configuring during boot) and reconnect. Use the EC2 console "Get system log" / "Get instance screenshot" to watch "Working on updates" progress - do not stop the instance while that screen is shown.
- If the instance never comes back online in Fleet Manager: check instance status checks, then stop/start (not reboot). `AWSSupport-ExecuteEC2Rescue` can diagnose offline.
- If an install fails, uninstall a bad KB with `wusa /uninstall /kb:<number>` (or DISM `/Remove-Package` for cumulative updates).
- Installing via the Windows Update API from a remote/non-interactive session can fail with access denied (0x80070005); running the install as SYSTEM (scheduled task, `Invoke-WUJob`, or Run Command) avoids it.
- Patch Manager `Operation=Install` with `RebootOption=RebootIfNeeded` reboots the instance automatically - run `Scan` first in this lab.

## Interview angle

"How do you patch 200 Windows servers on EC2?" - SSM Patch Manager: patch baselines (auto-approval rules by classification/severity), patch groups or tag targets, maintenance windows running `AWS-RunPatchBaseline` with rate control and error thresholds, compliance reporting; snapshots/AMIs before, staged rollout dev > test > prod. Logging on to each server is the exception, for troubleshooting.

[Solution](solutions/windows-05-security-updates.md) — try it first.
