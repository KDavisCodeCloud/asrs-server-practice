# Solution: Windows 07 - Investigate errors in Event Viewer

Run in an SSM PowerShell session (ssm-user is an administrator, which is required to read the Security log).

```powershell
New-Item -ItemType Directory C:\Lab -Force | Out-Null
$since = (Get-Date).AddHours(-24)
```

## 1. Logs and providers

```powershell
Get-WinEvent -ListLog Application, System, Security, Setup |
    Format-Table LogName, RecordCount, @{n='SizeMB';e={[math]::Round($_.FileSize/1MB,1)}}, @{n='MaxMB';e={$_.MaximumSizeInBytes/1MB}}, LogMode

Get-WinEvent -ListLog *TaskScheduler*, *WindowsUpdate* -ErrorAction SilentlyContinue | Select-Object LogName, IsEnabled, RecordCount
(Get-WinEvent -ListProvider 'Service Control Manager').LogLinks | Select-Object LogName
```

```
LogName     RecordCount SizeMB MaxMB LogMode
-------     ----------- ------ ----- -------
Application        2310    2.1    20 Circular
System             4120    4.1    20 Circular
Security          18011   20.0    20 Circular
```

Note `LogMode Circular`: when the Security log hits its maximum, old events are overwritten. On a busy server 20 MB can be only hours of history - forwarding logs off the box (CloudWatch agent, SIEM) is the real fix.

`Get-EventLog` only reads classic logs (Application/System/Security) and is not available in PowerShell 7; `Get-WinEvent` reads every log including the `Microsoft-Windows-*/Operational` ones and filters at the source.

## 2. Critical and Error events, grouped

```powershell
$sysErr = Get-WinEvent -FilterHashtable @{ LogName = 'System'; Level = 1, 2; StartTime = $since } -ErrorAction SilentlyContinue
$sysErr | Group-Object ProviderName, Id | Sort-Object Count -Descending |
    Select-Object Count, Name, @{n='Latest';e={ ($_.Group | Sort-Object TimeCreated -Descending)[0].TimeCreated }}
```

```
Count Name                                                Latest
----- ----                                                ------
    1 Service Control Manager, 7000                       10/23/2026 5:02:13 PM
    1 Microsoft-Windows-Kernel-Power, 41                  10/23/2026 5:21:44 PM
    1 EventLog, 6008                                      10/23/2026 5:21:50 PM
    1 Microsoft-Windows-WER-SystemErrorReporting, 1001    10/23/2026 5:22:30 PM
```

Levels: 1 Critical, 2 Error, 3 Warning, 4 Information (0 = LogAlways, often used by Security audit events).

## 3. Reboot timeline

```powershell
Get-WinEvent -FilterHashtable @{ LogName = 'System'; Id = 41, 1074, 1076, 6005, 6006, 6008, 6013, 1001; StartTime = $since } |
    Sort-Object TimeCreated |
    Select-Object TimeCreated, Id, ProviderName, @{n='Msg';e={ ($_.Message -split "`n")[0] }} |
    Format-Table -Wrap
```

```
TimeCreated           Id   ProviderName                                Msg
-----------           --   ------------                                ---
10/23/2026 5:10:02 PM 1074 User32                                      The process C:\Windows\system32\wbem\wmiprvse.exe (EC2AMAZ-XXXXXXX) has initiated the restart of computer EC2AMAZ-XXXXXXX on behalf of user EC2AMAZ-XXXXXXX\ssm-user ...
10/23/2026 5:10:05 PM 6006 EventLog                                    The Event log service was stopped.
10/23/2026 5:11:20 PM 6005 EventLog                                    The Event log service was started.
10/23/2026 5:11:20 PM 6013 EventLog                                    The system uptime is 14 seconds.
10/23/2026 5:21:44 PM   41 Microsoft-Windows-Kernel-Power              The system has rebooted without cleanly shutting down first...
10/23/2026 5:21:50 PM 6005 EventLog                                    The Event log service was started.
10/23/2026 5:21:50 PM 6008 EventLog                                    The previous system shutdown at 5:19:31 PM on 10/23/2026 was unexpected.
10/23/2026 5:22:30 PM 1001 Microsoft-Windows-WER-SystemErrorReporting  The computer has rebooted from a bugcheck. The bugcheck was: 0x00000080 (...)
```

Interpretation:

- **Clean (fault 4):** 1074 names the initiating process and user, with reason/shutdown type; then 6006 (EventLog stopped) and on boot 6005/6013. `Restart-Computer` goes through WMI, so the process is `wmiprvse.exe`; `shutdown /r` would show `shutdown.exe`.
- **Dirty (fault 5):** no 1074 and no 6006 before the gap. On boot: 41 (Kernel-Power, logged at boot about the previous session), 6008 with the time the OS last recorded, and 1001 BugCheck `0x80` (NMI_HARDWARE_FAILURE - the diagnostic interrupt). A power loss or hard hang shows 41 + 6008 without 1001. 1076 appears when someone documents an unexpected shutdown reason at the next logon (Shutdown Event Tracker).

Event 41's `BugcheckCode` property is non-zero when a bug-check caused it:

```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; Id=41; StartTime=$since} |
    ForEach-Object { ([xml]$_.ToXml()).Event.EventData.Data | Where-Object Name -in 'BugcheckCode','PowerButtonTimestamp' }
```

If `send-diagnostic-interrupt` produced no crash, use Sysinternals NotMyFault (`notmyfault64.exe /accepteula /crash`) as an alternative way to bug-check the box.

## 4. Service failure

```powershell
Get-WinEvent -FilterHashtable @{ LogName = 'System'; ProviderName = 'Service Control Manager'; Id = 7000, 7001, 7009, 7023, 7024, 7031, 7034; StartTime = $since } |
    Format-List TimeCreated, Id, Message
```

```
TimeCreated : 10/23/2026 5:02:13 PM
Id          : 7000
Message     : The LabBrokenSvc service failed to start due to the following error:
              The system cannot find the file specified.
```

Reference: 7000 failed to start (Win32 error in message), 7001 dependency failed, 7009 timeout (30 s default) waiting for the service to connect, 7023/7024 service terminated with an error, 7031 crashed and recovery action will run, 7034 crashed with no recovery configured, 7036 state change (information), 7040 start type changed, 7045 new service installed (worth alerting on - also generated here by `New-Service`).

## 5. Application crash

```powershell
Get-WinEvent -FilterHashtable @{ LogName = 'Application'; ProviderName = 'Application Error', '.NET Runtime', 'Windows Error Reporting'; StartTime = $since } |
    Sort-Object TimeCreated | Format-List TimeCreated, ProviderName, Id, Message
```

```
ProviderName : .NET Runtime
Id           : 1025
Message      : Application: powershell.exe ... Message: INC-3240 lab crash ...

ProviderName : Application Error
Id           : 1000
Message      : Faulting application name: powershell.exe, version: 10.0.20348.xxxx, ...
               Faulting module name: clr.dll (or KERNELBASE.dll) ...
               Exception code: 0x80131623
               Faulting process id: 0x1a2c
               Faulting application path: C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe

ProviderName : Windows Error Reporting
Id           : 1001
Message      : Fault bucket ..., type 5 Event Name: APPCRASH ...
```

Application Error 1000 is the standard "process crashed" event: application, faulting module, exception code (0xC0000005 = access violation, 0xC0000409 = stack buffer overrun, 0x80131623 = .NET FailFast). 1026 is the .NET "unhandled exception" variant; 1002 is Application Hang.

## 6. Failed logons

```powershell
$fail = Get-WinEvent -FilterHashtable @{ LogName = 'Security'; Id = 4625; StartTime = $since }
$fail | ForEach-Object {
    $p = $_.Properties
    [pscustomobject]@{
        Time        = $_.TimeCreated
        TargetUser  = "$($p[6].Value)\$($p[5].Value)"
        LogonType   = $p[10].Value
        Status      = '0x{0:X8}' -f $p[7].Value
        SubStatus   = '0x{0:X8}' -f $p[9].Value
        Process     = $p[18].Value
        SourceIP    = $p[19].Value
        Workstation = $p[13].Value
    }
} | Format-Table -AutoSize
```

```
Time                  TargetUser                    LogonType Status     SubStatus  Process SourceIP
----                  ----------                    --------- ------     ---------  ------- --------
10/23/2026 5:00:41 PM EC2AMAZ-XXXXXXX\nosuchuser            3 0xC000006D 0xC0000064 -       ::1
10/23/2026 5:00:42 PM EC2AMAZ-XXXXXXX\Administrator         3 0xC000006D 0xC000006A -       ::1
```

Property indexes for 4625: 5 TargetUserName, 6 TargetDomainName, 7 Status, 9 SubStatus, 10 LogonType, 13 WorkstationName, 18 ProcessName, 19 IpAddress. Check them for any event with `([xml]$e.ToXml()).Event.EventData.Data` if unsure.

Codes:

| Code | Meaning |
|---|---|
| `0xC000006D` | Generic "bad user name or password" (Status) - read the SubStatus |
| `0xC0000064` | User name does not exist (account enumeration / typo / stale service account) |
| `0xC000006A` | Correct user name, wrong password |
| `0xC0000072` | Account disabled |
| `0xC0000234` | Account locked out |
| `0xC0000071` / `0xC0000193` | Password expired / account expired |

Logon types: 2 interactive (console), 3 network (SMB, WinRM), 4 batch (scheduled task), 5 service, 7 unlock, 10 RemoteInteractive (RDP), 11 cached. Here type 3 from `::1` = SMB to localhost. Lockouts are event 4740; successful logons 4624.

Summary per account:

```powershell
$fail | Group-Object { $_.Properties[5].Value } | Select-Object Count, Name
```

## 7. Audit policy

```powershell
auditpol /get /subcategory:"Logon","Account Lockout","Special Logon"
```

```
Logon/Logoff
  Logon                                   Success and Failure
  Account Lockout                         Success
  Special Logon                           Success
```

This is the Server 2022 default. If 4625 events were missing, failure auditing for "Logon" would be off (`auditpol /set /subcategory:"Logon" /failure:enable`, or via Group Policy "Advanced Audit Policy Configuration").

## 8. Export evidence

Filtered `.evtx` (opens in any Event Viewer, keeps full fidelity):

```powershell
$xp24h = 'TimeCreated[timediff(@SystemTime) <= 86400000]'
wevtutil epl Security C:\Lab\INC-3240-security.evtx "/q:*[System[(EventID=4625) and $xp24h]]" /ow:true
wevtutil epl System   C:\Lab\INC-3240-system.evtx   "/q:*[System[(EventID=41 or EventID=1074 or EventID=6005 or EventID=6006 or EventID=6008 or EventID=1001 or EventID=7000) and $xp24h]]" /ow:true
wevtutil epl Application C:\Lab\INC-3240-app.evtx   "/q:*[System[(EventID=1000 or EventID=1025 or EventID=1026) and $xp24h]]" /ow:true
Get-WinEvent -Path C:\Lab\INC-3240-system.evtx | Measure-Object
```

CSV (one combined timeline):

```powershell
$timeline = @(
    Get-WinEvent -FilterHashtable @{ LogName='System'; Id=41,1074,6005,6006,6008,1001,7000; StartTime=$since }
    Get-WinEvent -FilterHashtable @{ LogName='Application'; Id=1000,1025,1026; StartTime=$since } -ErrorAction SilentlyContinue
    Get-WinEvent -FilterHashtable @{ LogName='Security'; Id=4625; StartTime=$since }
) | Sort-Object TimeCreated |
    Select-Object TimeCreated, LogName, ProviderName, Id, LevelDisplayName, @{n='Message';e={ ($_.Message -split "`r?`n")[0] }}
$timeline | Export-Csv C:\Lab\INC-3240-timeline.csv -NoTypeInformation
$timeline | Format-Table -AutoSize
```

The same query as XPath (useful in Event Viewer "Custom View > XML" and with `-FilterXPath`):

```powershell
Get-WinEvent -LogName System -FilterXPath "*[System[(Level=1 or Level=2) and $xp24h]]"
```

To get the files off the server without opening any ports, upload them to S3 would need extra IAM permissions; in this lab copy the CSV content through the SSM session (`Get-Content`) or via the RDP clipboard.

## 9. Example ticket timeline

```
17:00:41-17:00:44  5x 4625 type 3 from ::1 (local SMB): 3x unknown user 'nosuchuser' (0xC0000064), 2x bad password for Administrator (0xC000006A). No lockout (4740 absent).
17:02:13  SCM 7000: LabBrokenSvc failed to start - binary C:\Lab\missing.exe not found. 7045 shows it was installed by ssm-user at 17:02:10.
17:05:30  Application Error 1000 / .NET 1025: powershell.exe FailFast, exception 0x80131623.
17:10:02  1074 planned restart initiated by ssm-user via WmiPrvSE; 6006/6005 clean; back up 17:11:20.
17:19:31  Unexpected shutdown: no 1074; 41 + 6008 on boot at 17:21:44; 1001 BugCheck 0x80 (NMI, triggered by EC2 SendDiagnosticInterrupt - verify in CloudTrail).
```

## Cleanup / reset

```powershell
sc.exe delete LabBrokenSvc                  # Remove-Service only exists in PowerShell 6+
net use * /delete /y 2>$null
Remove-Item C:\Lab -Recurse -Force
Remove-Item C:\Windows\MEMORY.DMP -ErrorAction SilentlyContinue        # dump from the bug-check
Get-ChildItem C:\Windows\Minidump -ErrorAction SilentlyContinue | Remove-Item
```

Do not clear the event logs to "reset" - the next run simply uses a new `StartTime`.

## Common mistakes

- `Get-WinEvent -LogName System | Where-Object Id -eq 41` - reads the whole log first; slow and memory-heavy on a 2 GiB box. Use `-FilterHashtable`.
- Running as a non-admin and concluding there are no Security events (you get "No events were found" or access denied).
- Reading 6008's time as the crash time - it is the last time the OS recorded it was alive (heartbeat), so the real event can be a little later.
- Treating 41 as the cause; it only says "not clean" - the cause comes from 1001, the dump, hardware/hypervisor evidence or AWS status checks.
- Counting `Status 0xC000006D` without reading `SubStatus`.
- Forgetting that times are shown in the server's local time zone (EC2 Windows defaults to UTC) when correlating with CloudTrail (UTC) and your laptop.
