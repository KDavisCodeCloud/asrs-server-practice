# Solution: Windows 05 - Windows security updates

## 1. Current build and recent updates

```powershell
Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' |
    Select-Object ProductName, DisplayVersion, CurrentBuild, UBR, @{n='Build';e={"$($_.CurrentBuild).$($_.UBR)"}}
```

```
ProductName                      DisplayVersion CurrentBuild  UBR Build
-----------                      -------------- ------------  --- -----
Windows Server 2022 Datacenter   21H2           20348        xxxx 20348.xxxx
```

Server 2022 is always build 20348; the UBR (Update Build Revision) is what changes with each cumulative update - compare it with the "OS Build" in the Microsoft KB article for the month.

```powershell
Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 5 HotFixID, Description, InstalledOn
```

```
HotFixID  Description     InstalledOn
--------  -----------     -----------
KB50xxxxx Security Update 10/12/2026 12:00:00 AM
KB50xxxxx Update          10/12/2026 12:00:00 AM
```

Limits of `Get-HotFix` (it reads `Win32_QuickFixEngineering`): it lists component-servicing packages only - not Defender definition updates, drivers, or most .NET/Store updates - and `InstalledOn` has no time and is sometimes empty. AWS Windows AMIs are rebuilt monthly, so a fresh launch is often already current.

Full Windows Update Agent history (everything, with result codes):

```powershell
$searcher = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
$searcher.QueryHistory(0, $searcher.GetTotalHistoryCount()) |
    Select-Object -First 10 Date, @{n='Result';e={ @{1='InProgress';2='Succeeded';3='SucceededWithErrors';4='Failed';5='Aborted'}[$_.ResultCode] }}, Title
```

(`Date` is UTC.)

## 2. Pending reboot?

```powershell
function Test-PendingReboot {
    [pscustomobject]@{
        CBS_RebootPending   = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending'
        WU_RebootRequired   = Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired'
        PendingFileRename   = [bool](Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations -ErrorAction SilentlyContinue)
    }
}
Test-PendingReboot
```

The first two are authoritative. `PendingFileRename` is set by many installers and is a weaker signal. If a reboot is already pending, reboot before patching so you start from a clean state.

## 3. Snapshot first

Workstation:

```bash
ROOT_DEV=$(aws ec2 describe-instances --instance-ids "$WIN_ID" --query 'Reservations[0].Instances[0].RootDeviceName' --output text)
ROOT_VOL=$(aws ec2 describe-instances --instance-ids "$WIN_ID" \
  --query "Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName=='$ROOT_DEV'].Ebs.VolumeId" --output text)
SNAP=$(aws ec2 create-snapshot --volume-id "$ROOT_VOL" --description "CHG-4502 pre-patch asrs-lab-windows" \
  --tag-specifications 'ResourceType=snapshot,Tags=[{Key=Name,Value=asrs-lab-windows-prepatch}]' \
  --query SnapshotId --output text)
echo "$SNAP"
aws ec2 wait snapshot-completed --snapshot-ids "$SNAP"
```

The snapshot is crash-consistent. For a single-volume Windows server that is normally fine; for stricter consistency stop the instance first or use VSS-enabled snapshots (`AWSEC2-CreateVssSnapshot`, which needs extra IAM permissions not in this lab's role).

## 4. Scan for missing updates

Built-in Windows Update Agent API (works in the SSM session for searching):

```powershell
$session  = New-Object -ComObject Microsoft.Update.Session
$searcher = $session.CreateUpdateSearcher()
$result   = $searcher.Search("IsInstalled=0 and Type='Software' and IsHidden=0")
$result.Updates | Select-Object `
    @{n='KB';e={ ($_.KBArticleIDs | ForEach-Object { "KB$_" }) -join ',' }},
    @{n='Classification';e={ ($_.Categories | Where-Object Type -eq 'UpdateClassification').Name }},
    MsrcSeverity, @{n='SizeMB';e={ [math]::Round($_.MaxDownloadSize/1MB) }}, Title |
    Format-Table -AutoSize -Wrap
```

```
KB        Classification   MsrcSeverity SizeMB Title
--        --------------   ------------ ------ -----
KB50xxxxx Security Updates Critical        xxx 2026-10 Cumulative Update for Microsoft server operating system version 21H2 for x64-based Systems (KB50xxxxx)
KB50xxxxx Security Updates Important        xx 2026-10 Cumulative Update for .NET Framework 3.5, 4.8 and 4.8.1 ...
KB2267602 Definition Updates               xxx Security Intelligence Update for Microsoft Defender Antivirus - KB2267602 ...
```

The search can take 5-15 minutes on a t3.small. Zero results means the server is current.

Other built-in triggers (no output, fire-and-forget; watch progress with the events in step 7):

```powershell
UsoClient.exe StartScan      # undocumented orchestrator client, same engine as Settings
sconfig                      # Server Configuration tool, option 6 "Install updates" (interactive menu)
```

Or RDP (exercise 01) > Settings > Windows Update > Check for updates.

## 5. Install

### Method A (built-in, recommended here): WUA API as SYSTEM via a one-off scheduled task

Installing through the WUA COM API from a non-interactive/remote logon can fail with `0x80070005` (access denied). Running the same code as SYSTEM avoids that, survives the SSM session closing, and writes a log you can follow.

```powershell
New-Item -ItemType Directory C:\LabPatch -Force | Out-Null
@'
$log = 'C:\LabPatch\install.log'
function Log($m) { "$(Get-Date -Format s) $m" | Out-File $log -Append -Encoding utf8 }
Log 'search start'
$s = New-Object -ComObject Microsoft.Update.Session
$r = $s.CreateUpdateSearcher().Search("IsInstalled=0 and Type='Software' and IsHidden=0")
$coll = New-Object -ComObject Microsoft.Update.UpdateColl
foreach ($u in $r.Updates) {
    $class = ($u.Categories | Where-Object Type -eq 'UpdateClassification').Name
    if ($class -in 'Security Updates','Critical Updates') {
        if (-not $u.EulaAccepted) { $u.AcceptEula() }
        [void]$coll.Add($u); Log "queued: $($u.Title)"
    }
}
if ($coll.Count -eq 0) { Log 'nothing to install'; return }
$d = $s.CreateUpdateDownloader(); $d.Updates = $coll
Log "download result: $($d.Download().ResultCode)"
$i = $s.CreateUpdateInstaller(); $i.Updates = $coll
$ir = $i.Install()
for ($n = 0; $n -lt $coll.Count; $n++) {
    $ur = $ir.GetUpdateResult($n)
    Log ('{0} -> result {1} hresult 0x{2:X8}' -f $coll.Item($n).Title, $ur.ResultCode, $ur.HResult)
}
Log "install result: $($ir.ResultCode) rebootRequired: $($ir.RebootRequired)"
'@ | Set-Content C:\LabPatch\install.ps1 -Encoding UTF8

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\LabPatch\install.ps1'
Register-ScheduledTask -TaskName 'LabPatch-Install' -Action $action -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
Start-ScheduledTask -TaskName 'LabPatch-Install'
Get-Content C:\LabPatch\install.log -Wait      # Ctrl+C to stop following
```

Expected log:

```
2026-10-23T15:02:11 search start
2026-10-23T15:09:40 queued: 2026-10 Cumulative Update for Microsoft server operating system ... (KB50xxxxx)
2026-10-23T15:21:05 download result: 2
2026-10-23T15:58:33 2026-10 Cumulative Update ... -> result 2 hresult 0x00000000
2026-10-23T15:58:33 install result: 2 rebootRequired: True
```

Result codes: 2 = Succeeded, 3 = SucceededWithErrors, 4 = Failed, 5 = Aborted. A non-zero HRESULT is the error to search for (for example `0x800F0922`, `0x80070005`).

### Method B: PSWindowsUpdate (third-party, PowerShell Gallery)

Community module by Michal Gajda - widely used but not a Microsoft product. Mention it in interviews as an option, not as the standard.

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force
Install-Module PSWindowsUpdate -Scope AllUsers -Force
Get-WindowsUpdate -MicrosoftUpdate
Install-WindowsUpdate -MicrosoftUpdate -Category 'Security Updates' -AcceptAll -IgnoreReboot -Verbose
# If that fails with access denied, run it as SYSTEM:
Invoke-WUJob -ComputerName localhost -RunNow -Confirm:$false -Script {
    Import-Module PSWindowsUpdate
    Install-WindowsUpdate -MicrosoftUpdate -Category 'Security Updates' -AcceptAll -IgnoreReboot |
        Out-File C:\LabPatch\pswu.log
}
Get-WURebootStatus
Get-WUHistory | Select-Object -First 10
```

### Method C: Patch Manager - see step 9.

## 6. Reboot

```powershell
Test-PendingReboot
Restart-Computer -Force
```

The SSM session drops. From the workstation, watch it come back:

```bash
aws ec2 get-console-screenshot --instance-id "$WIN_ID" --query ImageData --output text | base64 -d > screen.jpg
aws ssm describe-instance-information --filters Key=InstanceIds,Values="$WIN_ID" --query 'InstanceInformationList[0].PingStatus'
```

`PingStatus` returns to `Online` when the agent is back; then reconnect with `aws ssm start-session`. A cumulative update can show "Working on updates" for 5-15 minutes on this instance size.

## 7. Evidence

```powershell
Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' | ForEach-Object { "$($_.CurrentBuild).$($_.UBR)" }
Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 5 HotFixID, Description, InstalledOn
Test-PendingReboot

Get-WinEvent -FilterHashtable @{
    LogName = 'System'; ProviderName = 'Microsoft-Windows-WindowsUpdateClient'
    Id = 19, 20, 43, 44; StartTime = (Get-Date).AddDays(-1)
} | Format-Table TimeCreated, Id, Message -Wrap
```

```
TimeCreated            Id Message
-----------            -- -------
10/23/2026 3:58:31 PM  19 Installation Successful: Windows successfully installed the following update: 2026-10 Cumulative Update ... (KB50xxxxx)
10/23/2026 3:22:10 PM  43 Installation Started: Windows has started installing the following update: ...
10/23/2026 3:10:02 PM  44 Windows Update started downloading an update.
```

Event 20 = installation failure, with the error code in the message. Servicing-stack detail is in the `Setup` log (`Get-WinEvent -LogName Setup -MaxEvents 10`: event 2 = package installed, 4 = reboot required) and in `C:\Windows\Logs\CBS\CBS.log`.

Re-run the scan from step 4 for "nothing missing" evidence.

## 8. WindowsUpdate.log

Since Windows 10/Server 2016, the Windows Update client writes ETW traces (`C:\Windows\Logs\WindowsUpdate\*.etl`), not a text log. `Get-WindowsUpdateLog` decodes them on demand:

```powershell
Get-WindowsUpdateLog -LogPath C:\LabPatch\WindowsUpdate.log
Select-String C:\LabPatch\WindowsUpdate.log -Pattern 'Search ClientId|Install ClientId|Exit code|FATAL|0x8' |
    Select-Object -Last 20
```

```
2026/10/23 15:02:12.1234567 1234  5678  ComApi   * START *   Search ClientId = ...
2026/10/23 15:58:30.1234567 1234  5678  Agent    ... Install ... Exit code = 0x00000000
```

It takes a minute or two and creates a temporary working folder. Related services when troubleshooting: `wuauserv` (Windows Update), `UsoSvc` (Update Orchestrator), `TrustedInstaller` (Windows Modules Installer), `BITS`.

Classic reset when scans fail repeatedly (for example `0x80244022`, corrupted cache):

```powershell
Stop-Service wuauserv, bits -Force
Rename-Item C:\Windows\SoftwareDistribution SoftwareDistribution.old
Start-Service bits, wuauserv
```

## 9. Patch Manager (AWS-native)

The instance role already has `AmazonSSMManagedInstanceCore`, which is enough for Run Command to execute `AWS-RunPatchBaseline`. With no custom baseline, Windows instances use the AWS default baseline for Windows (approves Critical and Security updates 7 days after release).

Workstation - scan only (no installation, no reboot):

```bash
CMD=$(aws ssm send-command --instance-ids "$WIN_ID" --document-name AWS-RunPatchBaseline \
  --parameters 'Operation=Scan' --comment 'CHG-4502 scan' --query Command.CommandId --output text)
aws ssm list-command-invocations --command-id "$CMD" --query 'CommandInvocations[0].Status' --output text
aws ssm describe-instance-patch-states --instance-ids "$WIN_ID" \
  --query 'InstancePatchStates[0].{Baseline:BaselineId,Installed:InstalledCount,Missing:MissingCount,Failed:FailedCount,Op:Operation,End:OperationEndTime}'
aws ssm describe-instance-patches --instance-id "$WIN_ID" --filters Key=State,Values=Missing \
  --query 'Patches[].[KBId,Classification,Severity,Title]' --output table
aws ssm describe-patch-baselines --filters Key=OWNER,Values=AWS Key=OPERATING_SYSTEM,Values=WINDOWS \
  --query 'BaselineIdentities[].[BaselineName,DefaultBaseline]' --output table
```

Install (reboots automatically if required):

```bash
aws ssm send-command --instance-ids "$WIN_ID" --document-name AWS-RunPatchBaseline \
  --parameters 'Operation=Install,RebootOption=RebootIfNeeded' --timeout-seconds 600 \
  --comment 'CHG-4502 install'
```

Results appear in Systems Manager > Patch Manager > Compliance reporting and in `describe-instance-patch-states`. At scale you would add: tag-based targets or patch groups, a maintenance window with `--max-concurrency`/`--max-errors`, a custom baseline (approval delay, rejected KBs), S3 output for command logs, and pre-patch AMIs via an Automation runbook.

## Cleanup / reset

```powershell
Unregister-ScheduledTask -TaskName 'LabPatch-Install' -Confirm:$false
Remove-Item C:\LabPatch -Recurse -Force
Remove-Item C:\Windows\SoftwareDistribution.old -Recurse -Force -ErrorAction SilentlyContinue
# If you used PSWindowsUpdate:
Uninstall-Module PSWindowsUpdate -AllVersions -Force
```

Installed updates are not rolled back (that is the point). To repeat the exercise "unpatched", restore from the pre-patch snapshot (Replace root volume in the EC2 console, or `aws ec2 create-replace-root-volume-task --instance-id "$WIN_ID" --snapshot-id "$SNAP"`), or redeploy the stack. Delete the snapshot when finished: `aws ec2 delete-snapshot --snapshot-id "$SNAP"`.

## Common mistakes

- Treating `Get-HotFix` as the complete list of installed updates.
- Comparing the build number (20348) instead of the UBR.
- Rebooting or stopping the instance while "Working on updates" is shown - risks a failed servicing operation and an unbootable volume.
- Running the install in the SSM session, closing the laptop, and killing the install with the session; run it as a task/Run Command.
- Patching without a snapshot/AMI and without checking for a pending reboot first.
- Expecting `Get-WindowsUpdateLog` to show live output - it is a point-in-time decode of the ETL files.
- Running `AWS-RunPatchBaseline` with `Operation=Install` in production without a maintenance window, rate control or a scan first.
- Forgetting T3 credits: a long install on a t3.small in standard mode can exhaust CPU credits and throttle the box to 20%.
