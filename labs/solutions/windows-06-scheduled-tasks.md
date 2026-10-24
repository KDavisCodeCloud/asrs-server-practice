# Solution: Windows 06 - Scheduled PowerShell tasks

## 1. The script

```powershell
New-Item -ItemType Directory C:\Ops\Scripts, C:\Ops\Logs -Force | Out-Null

@'
param([int]$ThresholdPct = 15)
$ErrorActionPreference = 'Stop'
$log   = 'C:\Ops\Logs\disk-report.log'
$stamp = Get-Date -Format 'yyyy-MM-ddTHH:mm:ss'
$low   = $false
try {
    Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | ForEach-Object {
        $pct = [math]::Round(100 * $_.FreeSpace / $_.Size, 1)
        if ($pct -lt $ThresholdPct) { $low = $true }
        '{0} {1} sizeGB={2:N1} freeGB={3:N1} free%={4} runAs={5}' -f `
            $stamp, $_.DeviceID, ($_.Size / 1GB), ($_.FreeSpace / 1GB), $pct, (whoami) |
            Add-Content -Path $log
    }
} catch {
    "$stamp ERROR $($_.Exception.Message)" | Add-Content -Path $log
    exit 1
}
if ($low) { exit 2 }
exit 0
'@ | Set-Content C:\Ops\Scripts\Get-DiskReport.ps1 -Encoding UTF8
```

Exit codes: 0 = OK, 1 = script error, 2 = low disk. Task Scheduler records the process exit code as `LastTaskResult`, so meaningful exit codes are what make failures visible.

Lock down the script folder - the task will run as SYSTEM, so anyone who can write here could run code as SYSTEM:

```powershell
icacls C:\Ops\Scripts /inheritance:r /grant:r 'NT AUTHORITY\SYSTEM:(OI)(CI)F' 'BUILTIN\Administrators:(OI)(CI)F'
```

Test it interactively first:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Ops\Scripts\Get-DiskReport.ps1; $LASTEXITCODE
Get-Content C:\Ops\Logs\disk-report.log -Tail 2
```

```
0
2026-10-23T16:00:02 C: sizeGB=40.0 freeGB=21.7 free%=54.3 runAs=ec2amaz-xxxxxxx\ssm-user
```

## 2. Execution policy

```powershell
Get-ExecutionPolicy -List
```

```
        Scope ExecutionPolicy
        ----- ---------------
MachinePolicy       Undefined
   UserPolicy       Undefined
      Process       Undefined
  CurrentUser       Undefined
 LocalMachine    RemoteSigned
```

Server 2022 default is `RemoteSigned`: local scripts run, scripts with a Mark-of-the-Web (downloaded) need a signature or `Unblock-File`. Decision: leave the machine policy alone and pass `-ExecutionPolicy Bypass` on the task's command line (Process scope). Note `MachinePolicy`/`UserPolicy` (Group Policy) override `-ExecutionPolicy` - if those were set, you would sign the script instead.

## 3. Enable history

```powershell
wevtutil get-log Microsoft-Windows-TaskScheduler/Operational | Select-String enabled   # enabled: false
wevtutil set-log Microsoft-Windows-TaskScheduler/Operational /enabled:true
```

This is the same as "Enable All Tasks History" in Task Scheduler's Actions pane.

## 4-5. Register the 15-minute task

```powershell
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "C:\Ops\Scripts\Get-DiskReport.ps1"' `
    -WorkingDirectory 'C:\Ops\Scripts'

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 15)          # no -RepetitionDuration = indefinitely

$principal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\SYSTEM' -LogonType ServiceAccount -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskPath '\Ops\' -TaskName 'DiskReport' -Description 'REQ-2290 disk report' `
    -Action $action -Trigger $trigger -Principal $principal -Settings $settings
```

```
TaskPath  TaskName    State
--------  --------    -----
\Ops\     DiskReport  Ready
```

Principal choice:

| Option | Runs when logged off | Stored password | Network access | Notes |
|---|---|---|---|---|
| `SYSTEM` (`ServiceAccount`) | Yes | No | As computer account | Chosen here: local-only job, no password to rotate. Most privileged - protect the script. |
| Local user + password (`Password` logon type) | Yes | Yes (LSA secret) | Yes | Breaks when the password changes; needs "Log on as a batch job" (Administrators and Backup Operators have it by default). |
| Local user, `S4U` | Yes | No | No | Good least-privilege option for local-only work without a stored password. |
| gMSA | Yes | Managed by AD | Yes | Best practice in a domain; not possible on this standalone lab server. |
| `Interactive` | Only when that user is logged on | No | Yes | Not acceptable for this ticket. |

"Run whether user is logged on or not" in the GUI corresponds to the `Password`, `S4U` or `ServiceAccount` logon types.

Equivalent with schtasks (for reference):

```powershell
schtasks.exe /Create /TN "\Ops\DiskReport" /RU SYSTEM /RL HIGHEST /SC MINUTE /MO 15 `
    /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Ops\Scripts\Get-DiskReport.ps1"
```

## 6. Startup task

```powershell
$startTrigger = New-ScheduledTaskTrigger -AtStartup
$startTrigger.Delay = 'PT1M'          # give networking/services a minute
Register-ScheduledTask -TaskPath '\Ops\' -TaskName 'DiskReport-Startup' `
    -Action $action -Trigger $startTrigger -Principal $principal -Settings $settings
```

## 7. Run and prove

```powershell
Start-ScheduledTask -TaskPath '\Ops\' -TaskName 'DiskReport'
Start-Sleep 10
Get-ScheduledTaskInfo -TaskPath '\Ops\' -TaskName 'DiskReport' |
    Select-Object LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns
Get-Content C:\Ops\Logs\disk-report.log -Tail 1
```

```
LastRunTime          LastTaskResult NextRunTime          NumberOfMissedRuns
-----------          -------------- -----------          ------------------
10/23/2026 4:05:10PM              0 10/23/2026 4:16:00PM                  0

2026-10-23T16:05:11 C: sizeGB=40.0 freeGB=21.7 free%=54.3 runAs=nt authority\system
```

`runAs=nt authority\system` proves the principal. History:

```powershell
function Get-TaskHistory($FullName, [int]$Minutes = 30) {
    Get-WinEvent -FilterHashtable @{
        LogName = 'Microsoft-Windows-TaskScheduler/Operational'; StartTime = (Get-Date).AddMinutes(-$Minutes)
    } -ErrorAction SilentlyContinue |
        Where-Object Message -like "*`"$FullName`"*" |
        Sort-Object TimeCreated |
        Format-Table TimeCreated, Id, Message -Wrap
}
Get-TaskHistory '\Ops\DiskReport'
```

```
Id  Message
--  -------
106 User "EC2AMAZ-XXXXXXX\ssm-user"  registered Task Scheduler task "\Ops\DiskReport"
110 Task Scheduler launched "{...}"  instance of task "\Ops\DiskReport"  for user "ssm-user" .
100 Task Scheduler started "{...}" instance of the "\Ops\DiskReport" task for user "NT AUTHORITY\System".
129 Task Scheduler launch task "\Ops\DiskReport" , instance "powershell.exe"  with process ID 4412.
200 Task Scheduler launched action "powershell.exe" in instance "{...}" of task "\Ops\DiskReport".
201 Task Scheduler successfully completed task "\Ops\DiskReport" , instance "{...}" , action "powershell.exe" with return code 0.
102 Task Scheduler successfully finished "{...}" instance of the "\Ops\DiskReport" task for user "NT AUTHORITY\System".
```

Key IDs: 106 registered, 140 updated, 141 deleted, 107 time trigger, 110 run by user, 118 boot trigger, 100 started, 129 process created, 200 action started, 201 action completed (with return code), 102 finished, 101 failed to start, 203 action failed to launch, 111 terminated (time limit), 322 launch ignored because an instance is already running.

## 8. Fault injection and reading the failures

```powershell
$bad1 = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\Ops\Scripts\Get-DiskReprot.ps1"'
Register-ScheduledTask -TaskPath '\Ops\' -TaskName 'Bad-Path' -Action $bad1 -Principal $principal | Out-Null

'exit 3' | Set-Content C:\Ops\Scripts\Exit3.ps1
$bad2 = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\Ops\Scripts\Exit3.ps1"'
Register-ScheduledTask -TaskPath '\Ops\' -TaskName 'Bad-Exit' -Action $bad2 -Principal $principal | Out-Null

Start-ScheduledTask -TaskPath '\Ops\' -TaskName 'Bad-Path'
Start-ScheduledTask -TaskPath '\Ops\' -TaskName 'Bad-Exit'
Start-Sleep 10
Get-ScheduledTask -TaskPath '\Ops\' | Get-ScheduledTaskInfo |
    Select-Object TaskName, LastRunTime, LastTaskResult, @{n='Hex';e={'0x{0:X8}' -f $_.LastTaskResult}}
```

```
TaskName           LastTaskResult Hex
--------           -------------- ---
Bad-Exit                        3 0x00000003
Bad-Path               4294770688 0xFFFD0000
DiskReport                      0 0x00000000
DiskReport-Startup         267011 0x00041303
```

- `0xFFFD0000` (-196608): `powershell.exe -File` could not find the script. PowerShell started fine; the script never ran.
- `3`: your script's own exit code.
- `0x41303` (267011): task has not run yet. `0x41301` (267009): currently running. `0x1`: generic failure (common with `-Command` scripts that throw). `0x8007052E`: logon failure (bad stored password).

Why it can look successful: Task Scheduler's job is to launch `powershell.exe`, and it did. Event 201 reads "successfully completed task ... with return code 4294770688", and 102 "successfully finished". The GUI's "Last Run Result" column shows the code, but people read the word "successfully" in history and stop. Always check the return code.

```powershell
Get-TaskHistory '\Ops\Bad-Path' 10
```

## 9. Export to XML

```powershell
Export-ScheduledTask -TaskPath '\Ops\' -TaskName 'DiskReport' | Out-File C:\Ops\DiskReport.xml -Encoding Unicode
Select-String C:\Ops\DiskReport.xml -Pattern 'UserId|RunLevel|Interval|Arguments'
```

Restore / re-create from XML:

```powershell
Register-ScheduledTask -TaskPath '\Ops\' -TaskName 'DiskReport' -Xml (Get-Content C:\Ops\DiskReport.xml -Raw) -Force
```

## 10. Reboot and prove the startup task

```powershell
Restart-Computer -Force
```

After reconnecting (wait 2-3 minutes; the trigger has a 1-minute delay):

```powershell
(Get-CimInstance Win32_OperatingSystem).LastBootUpTime
Get-ScheduledTaskInfo -TaskPath '\Ops\' -TaskName 'DiskReport-Startup' | Select-Object LastRunTime, LastTaskResult
Get-TaskHistory '\Ops\DiskReport-Startup' 15       # look for 118 (boot trigger) then 100/201/102
```

## Cleanup / reset

```powershell
Get-ScheduledTask -TaskPath '\Ops\' | Unregister-ScheduledTask -Confirm:$false
$svc = New-Object -ComObject Schedule.Service; $svc.Connect()
$svc.GetFolder('\').DeleteFolder('Ops', 0)          # remove the empty \Ops\ folder
Remove-Item C:\Ops -Recurse -Force
wevtutil set-log Microsoft-Windows-TaskScheduler/Operational /enabled:false   # optional - back to default
```

## Common mistakes

- `-Argument` without quotes around a path with spaces, or using `-Command` with a `.ps1` path containing spaces.
- Forgetting `-ExecutionPolicy Bypass` and relying on the machine policy (fine for local scripts under RemoteSigned, fails for downloaded/blocked scripts).
- Mapped drives (`Z:\`) in the script - they do not exist for SYSTEM or non-interactive sessions; use UNC paths.
- Relative paths in the script - the default working directory for tasks is `C:\Windows\System32`.
- Script writes errors to the console only - nothing captures it; log to a file and set exit codes.
- `-RepetitionDuration ([TimeSpan]::MaxValue)` from old blog posts - fails on current Windows; omit it for "indefinitely".
- History disabled, so there is no evidence when the ticket comes back.
- Using a personal admin account as the principal; the task breaks at the next password change or when that person leaves.
