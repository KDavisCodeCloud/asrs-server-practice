# Solution: Windows 04 - Diagnose Windows service failures

Assumes you saved the backups in `C:\LabBackup` and injected the faults as described in the exercise.

## Fault 1 - invalid binary path

### 1. Confirm the symptom

Linux:

```bash
curl -s -m 5 -o /dev/null -w '%{http_code}\n' http://$WIN_IP/
```

```
000        # curl: (7) Failed to connect ... Connection refused
```

"Connection refused" (a TCP reset), not a timeout: the packet reached the host and the firewall allowed it, but nothing is listening on 80. That already points at the service, not the network. (If some other http.sys registration still holds port 80 you may get an HTTP 404/503 from http.sys instead - same conclusion: network fine, web service not.)

Windows:

```powershell
Get-Service W3SVC | Format-List Name, Status, StartType, DisplayName
Start-Service W3SVC
sc.exe start W3SVC
```

```
Name      : W3SVC
Status    : Stopped
StartType : Automatic

Start-Service : Service 'World Wide Web Publishing Service (W3SVC)' cannot be started due to the
following error: Cannot start service W3SVC on computer '.'.

[SC] StartService FAILED 2:

The system cannot find the file specified.
```

`Start-Service` hides the Win32 error code; `sc.exe start` shows it (2 = ERROR_FILE_NOT_FOUND). Translate any code with `net helpmsg <code>`.

### 2. Inspect the configuration

```powershell
sc.exe qc W3SVC
```

```
[SC] QueryServiceConfig SUCCESS

SERVICE_NAME: W3SVC
        TYPE               : 20  WIN32_SHARE_PROCESS
        START_TYPE         : 2   AUTO_START
        ERROR_CONTROL      : 1   NORMAL
        BINARY_PATH_NAME   : C:\Windows\system32\svchost-missing.exe -k iissvcs
        LOAD_ORDER_GROUP   :
        TAG                : 0
        DISPLAY_NAME       : World Wide Web Publishing Service
        DEPENDENCIES       : WAS
                           : HTTP
        SERVICE_START_NAME : LocalSystem
```

```powershell
Test-Path C:\Windows\system32\svchost-missing.exe     # False
Get-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Services\W3SVC | Select-Object ImagePath, Start, DependOnService, ObjectName
```

### 3. The SCM event

```powershell
Get-WinEvent -FilterHashtable @{
    LogName = 'System'; ProviderName = 'Service Control Manager'
    Id = 7000, 7001, 7009, 7023, 7024; StartTime = (Get-Date).AddHours(-2)
} | Format-List TimeCreated, Id, Message
```

```
TimeCreated : 10/23/2026 2:41:07 PM
Id          : 7000
Message     : The World Wide Web Publishing Service service failed to start due to the following error:
              The system cannot find the file specified.
```

### 4. Root cause and fix

Root cause: `ImagePath` points to a non-existent executable. The saved backup shows the correct value:

```powershell
Select-String -Path C:\LabBackup\w3svc-qc.txt -Pattern BINARY_PATH_NAME
```

```
        BINARY_PATH_NAME   : C:\Windows\system32\svchost.exe -k iissvcs
```

```powershell
sc.exe config W3SVC binPath= "C:\Windows\system32\svchost.exe -k iissvcs"
```

```
[SC] ChangeServiceConfig SUCCESS
```

Alternative: `reg import C:\LabBackup\w3svc.reg` (restores the whole key).

### 5. Start and verify

```powershell
Start-Service W3SVC
Get-Service W3SVC, WAS
```

Linux: `curl -s -o /dev/null -w '%{http_code}\n' http://$WIN_IP/` returns `200`.

## Fault 2 - disabled dependency

### 6. Diagnose

```powershell
sc.exe start W3SVC
```

```
[SC] StartService FAILED 1068:

The dependency service or group failed to start.
```

`W3SVC` itself is fine; one of its dependencies is not. Show the chain both ways:

```powershell
Get-Service W3SVC -RequiredServices          # what W3SVC needs
Get-Service WAS   -DependentServices         # what needs WAS
Get-Service WAS | Select-Object Name, Status, StartType
sc.exe enumdepend WAS
```

```
Status   Name   DisplayName
------   ----   -----------
Stopped  WAS    Windows Process Activation Service
Running  HTTP   HTTP Service

Name StartType Status
---- --------- ------
WAS   Disabled Stopped
```

The event:

```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Service Control Manager'; Id=7001; StartTime=(Get-Date).AddMinutes(-30)} |
    Select-Object -First 1 -ExpandProperty Message
```

```
The World Wide Web Publishing Service service depends on the Windows Process Activation Service service
which failed to start because of the following error:
The service cannot be started, either because it is disabled or because it has no enabled devices associated with it.
```

Also look for event 7040 ("The start type of the Windows Process Activation Service service was changed from demand start to disabled.") - it shows who/when, which is what the post-incident review wants:

```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; Id=7040; StartTime=(Get-Date).AddDays(-1)} |
    Format-List TimeCreated, UserId, Message
```

### 7. Fix

Restore the original start type from the backup (`START_TYPE` in `was-qc.txt`; on Server 2022 it is normally `3 DEMAND_START` = Manual):

```powershell
Select-String C:\LabBackup\was-qc.txt -Pattern START_TYPE
Set-Service WAS -StartupType Manual
Start-Service W3SVC        # starts WAS automatically as a dependency
Get-Service W3SVC, WAS
```

## Fault 3 - unexpected termination

### 8. Events

Before injecting the fault, look at the recovery configuration and the hosting process:

```powershell
sc.exe qfailure W3SVC
$svcPid = (Get-CimInstance Win32_Service -Filter "Name='W3SVC'").ProcessId
tasklist /svc /fi "PID eq $svcPid"
```

```
Image Name                     PID Services
========================= ======== ============================================
svchost.exe                   3120 W3SVC, WAS
```

After the kill:

```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Service Control Manager'; Id=7031,7034; StartTime=(Get-Date).AddMinutes(-15)} |
    Format-List TimeCreated, Id, Message
```

Without recovery actions:

```
Id      : 7034
Message : The World Wide Web Publishing Service service terminated unexpectedly. It has done this 1 time(s).
```

With recovery actions:

```
Id      : 7031
Message : The World Wide Web Publishing Service service terminated unexpectedly. It has done this 1 time(s).
          The following corrective action will be taken in 60000 milliseconds: Restart the service.
```

7034 = crashed, no corrective action configured. 7031 = crashed, SCM will apply a recovery action. A clean `Stop-Service` produces neither - only 7036 ("entered the stopped state"). Both `W3SVC` and `WAS` appear because they shared the killed process.

### 9. Configure recovery

```powershell
sc.exe failure W3SVC reset= 86400 actions= restart/60000/restart/60000//
sc.exe failureflag W3SVC 1
sc.exe qfailure W3SVC
```

```
[SC] ChangeServiceConfig2 SUCCESS

SERVICE_NAME: W3SVC
        RESET_PERIOD (in seconds)    : 86400
        REBOOT_MESSAGE               :
        COMMAND_LINE                 :
        FAILURE_ACTIONS              : RESTART -- Delay = 60000 milliseconds.
                                       RESTART -- Delay = 60000 milliseconds.
```

- `reset= 86400` resets the failure count after 1 day without failures.
- `actions=` is `action/delay` pairs for 1st, 2nd and subsequent failures; the empty third pair means "take no action" on the third and later failures within the reset window, so a crash loop does not restart forever.
- `failureflag 1` also triggers recovery when the service stops itself with a non-zero exit code, not only when the process dies.

GUI equivalent: `services.msc` > World Wide Web Publishing Service > Recovery tab.

### 10. Prove self-healing

```powershell
Stop-Process -Id (Get-CimInstance Win32_Service -Filter "Name='W3SVC'").ProcessId -Force
Get-Service W3SVC                        # Stopped
Start-Sleep -Seconds 70
Get-Service W3SVC, WAS                   # Running
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Service Control Manager'; StartTime=(Get-Date).AddMinutes(-3)} |
    Format-Table TimeCreated, Id, Message -Wrap
```

You see 7031 (with "Restart the service") followed about 60 seconds later by 7036 "...entered the running state". Linux curl returns 200 again without manual action.

## Validation

```powershell
Get-Service W3SVC, WAS | Format-Table Name, Status, StartType
sc.exe qc W3SVC | Select-String BINARY_PATH_NAME
sc.exe qfailure W3SVC
```

## Cleanup / reset

To return W3SVC and WAS exactly to the pre-lab state:

```powershell
reg import C:\LabBackup\w3svc.reg
reg import C:\LabBackup\was.reg
# reg import adds/overwrites values but does not delete new ones.
# If the backup had no FailureActions value, remove the one you created:
if (-not (Select-String C:\LabBackup\w3svc.reg -Pattern 'FailureActions' -Quiet)) {
    Remove-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Services\W3SVC -Name FailureActions, FailureActionsOnNonCrashFailures -ErrorAction SilentlyContinue
}
Restart-Computer -Force     # SCM reads service config changes made via the registry at boot
```

Keep `C:\LabBackup` if you want to repeat; delete it when finished.

## Common mistakes

- Typing `sc qc W3SVC` in PowerShell - that is `Set-Content`; it silently creates a file named `qc`.
- `sc.exe config W3SVC binPath="..."` without the space after `=` - sc prints its usage text and changes nothing.
- Fixing the symptom with `Start-Service` after fault 3 but never adding recovery or monitoring.
- Setting the dependency back to `Automatic` instead of its original start type; it works but is configuration drift.
- Reading only the Application log - SCM events are in the System log.
- Restarting a single service name when `tasklist /svc` shows several services share the process - know the blast radius.
- Setting `reboot` as a recovery action on a production web server without approval.
