# Windows 04 - Diagnose Windows service failures

## Objective

Diagnose why a Windows service will not start or keeps stopping, using the service configuration, its dependencies, Service Control Manager events and recovery settings - then fix it and make it self-healing.

## Scenario

> **Ticket INC-3188** - Priority 2
> Monitoring reports the internal web page on `asrs-lab-windows` is down. A junior admin "tidied up some services" yesterday and then rebooted. `W3SVC` will not start. After you fix that, there is a second problem: the web service has died twice overnight with nobody restarting it. Restore service, find the root cause from evidence (not guesswork), and configure the service to recover automatically.

## Prerequisites

- Exercise 03 completed: IIS installed and `http://<win-private-ip>/` returns 200 from Linux.
- SSM PowerShell session on Windows; SSM shell on Linux for client-side checks.

### Set up the fault (you do this yourself)

Before breaking anything, save the original configuration to a file you can read later:

```powershell
New-Item -ItemType Directory C:\LabBackup -Force | Out-Null
sc.exe qc W3SVC   > C:\LabBackup\w3svc-qc.txt
sc.exe qc WAS     > C:\LabBackup\was-qc.txt
sc.exe qfailure W3SVC > C:\LabBackup\w3svc-qfailure.txt
reg export HKLM\SYSTEM\CurrentControlSet\Services\W3SVC C:\LabBackup\w3svc.reg /y
reg export HKLM\SYSTEM\CurrentControlSet\Services\WAS   C:\LabBackup\was.reg /y
```

Fault 1 - broken binary path (do this, then solve tasks 1-5):

```powershell
Stop-Service W3SVC -Force
sc.exe config W3SVC binPath= "C:\Windows\system32\svchost-missing.exe -k iissvcs"
```

Fault 2 - disabled dependency (after fixing fault 1, do this, then solve tasks 6-7):

```powershell
Stop-Service WAS -Force
Set-Service WAS -StartupType Disabled
```

Fault 3 - unexpected termination (after fixing fault 2, for tasks 8-10). Have a colleague (or you, then forget) kill the process hosting W3SVC:

```powershell
Stop-Process -Id (Get-CimInstance Win32_Service -Filter "Name='W3SVC'").ProcessId -Force
```

## What you need to accomplish

1. Confirm the symptom from both sides: client (Linux curl) and server (service state, start attempt and its exact error).
2. Inspect the service's configuration: binary path, start type, account, dependencies.
3. Find the System log event recorded by the Service Control Manager for the failed start and quote its message.
4. Identify the root cause of fault 1 and correct it using the evidence you saved, not memory.
5. Start the service and verify end to end from Linux.
6. For fault 2, determine why the start fails even though `W3SVC` itself is configured correctly. Show the dependency chain in both directions.
7. Fix fault 2 and verify.
8. For fault 3, find the events that record the termination and explain the difference between the two possible SCM event IDs for it.
9. Configure recovery so that `W3SVC` restarts automatically after the first and second failures, and the failure counter resets after one day.
10. Repeat fault 3 and prove the service comes back on its own; show the event that proves a recovery action was taken.

## Commands or tools to research

- `Get-Service` (`-RequiredServices`, `-DependentServices`), `Start-Service`, `Stop-Service`, `Set-Service`
- `Get-CimInstance Win32_Service`
- `sc.exe query`, `sc.exe queryex`, `sc.exe qc`, `sc.exe config`, `sc.exe qfailure`, `sc.exe failure`, `sc.exe failureflag`, `sc.exe enumdepend`
- Registry: `HKLM\SYSTEM\CurrentControlSet\Services\<name>` (`ImagePath`, `Start`, `DependOnService`)
- `Get-WinEvent` with `FilterHashtable` (`ProviderName = 'Service Control Manager'`)
- Event IDs 7000, 7001, 7009, 7023, 7024, 7031, 7034, 7036, 7040
- `net helpmsg`, `services.msc` (Recovery tab)

## Validation criteria

- `Get-Service W3SVC, WAS` - both `Running`, `StartType` `Automatic` (or `Manual` for WAS if that is what your saved backup shows).
- `sc.exe qc W3SVC` shows `BINARY_PATH_NAME : C:\Windows\system32\svchost.exe -k iissvcs` (matches your backup).
- `sc.exe qfailure W3SVC` shows `RESET_PERIOD (in seconds) : 86400` and `RESTART` actions.
- After killing the process, `W3SVC` is `Running` again within the configured delay, without you starting it, and Linux `curl` returns 200.
- You can show the 7000, 7001 and 7031 (or 7034) events with timestamps that match your fault injections.

## Recovery considerations

- `sc.exe config` edits the registry directly - one wrong `binPath` on a core service (for example `AmazonSSMAgent`, `RpcSs`, `LanmanServer`) can break SSM access or boot. Only touch `W3SVC`/`WAS` in this lab, and take a root-volume snapshot first if unsure.
- In PowerShell, `sc` is an alias for `Set-Content`. Always type `sc.exe`. The syntax needs a space after `=` (`binPath= "..."`).
- Your `.reg` exports restore the full service key: `reg import C:\LabBackup\w3svc.reg`, then reboot or restart the service.
- If you break `AmazonSSMAgent` itself, Session Manager and Run Command both stop working. Recovery is offline: stop the instance, use `AWSSupport-ExecuteEC2Rescue` or attach the root volume to a rescue instance and fix the registry hive; then start the instance.
- Killing the shared `svchost` process stops every service hosted in it (on 2 GiB instances Windows groups services into shared processes). Check with `tasklist /svc /fi "PID eq <pid>"` before you kill anything.

## Interview angle

"A service won't start - walk me through it." - Reproduce and capture the exact error code, `sc.exe qc` for path/account/dependencies, the SCM events (7000 start failure, 7001 dependency, 7009 timeout, 7031/7034 crash), fix the root cause, then set recovery actions and alerting so a crash is self-healing and visible.

[Solution](solutions/windows-04-service-failures.md) — try it first.
