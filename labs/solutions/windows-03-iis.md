# Solution: Windows 03 - Install and administer IIS

## 1. Install the role

```powershell
Get-WindowsFeature Web-Server, Web-Mgmt-Console, Web-Scripting-Tools
```

```
Display Name                                  Name                Install State
------------                                  ----                -------------
[ ] Web Server (IIS)                          Web-Server              Available
    [ ] IIS Management Console                Web-Mgmt-Console        Available
    [ ] IIS Management Scripts and Tools      Web-Scripting-Tools     Available
```

```powershell
Install-WindowsFeature -Name Web-Server -IncludeManagementTools
```

Expected (after several minutes on a t3.small):

```
Success Restart Needed Exit Code      Feature Result
------- -------------- ---------      --------------
True    No             Success        {Common HTTP Features, Default Document, D...
```

`-IncludeManagementTools` adds the IIS Manager console and the `WebAdministration`/`IISAdministration` modules. No reboot is needed.

## 2. Services and listener

```powershell
Get-Service W3SVC, WAS
Get-NetTCPConnection -LocalPort 80 -State Listen |
    Select-Object LocalAddress, LocalPort, OwningProcess,
        @{n='Process';e={(Get-Process -Id $_.OwningProcess).ProcessName}}
```

```
Status   Name    DisplayName
------   ----    -----------
Running  W3SVC   World Wide Web Publishing Service
Running  WAS     Windows Process Activation Service

LocalAddress LocalPort OwningProcess Process
------------ --------- ------------- -------
::                  80             4 System
0.0.0.0             80             4 System
```

PID 4 (`System`) is correct: the kernel driver http.sys owns port 80 and hands requests to the worker processes (`w3wp.exe`), which start on the first request.

Firewall rule created by the install:

```powershell
Get-NetFirewallRule -DisplayName 'World Wide Web Services (HTTP Traffic-In)' |
    Select-Object DisplayName, Enabled, Profile, Action
```

## 3. Private IP

```powershell
$token = Invoke-RestMethod -Method PUT -Uri 'http://169.254.169.254/latest/api/token' `
    -Headers @{ 'X-aws-ec2-metadata-token-ttl-seconds' = '300' }
$ip = Invoke-RestMethod -Uri 'http://169.254.169.254/latest/meta-data/local-ipv4' `
    -Headers @{ 'X-aws-ec2-metadata-token' = $token }
$ip      # e.g. 10.20.2.57
```

Or from the OS: `Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp | Select-Object IPAddress, InterfaceAlias`.

IMDSv2 is required on this instance, so a plain `Invoke-RestMethod http://169.254.169.254/latest/meta-data/local-ipv4` without a token returns 401.

## 4. Default page

```powershell
"<h1>ASRS default - $env:COMPUTERNAME</h1>" | Set-Content C:\inetpub\wwwroot\index.html -Encoding UTF8
```

`index.html` is before `iisstart.htm` in the default document list, so it is served for `/`.

## 5. App pool and host-header site

```powershell
Import-Module WebAdministration

New-Item -ItemType Directory C:\Sites\ops-status -Force | Out-Null
"<h1>ops-status OK - $env:COMPUTERNAME</h1>" | Set-Content C:\Sites\ops-status\index.html -Encoding UTF8

New-WebAppPool -Name 'ops-status-pool'
Set-ItemProperty IIS:\AppPools\ops-status-pool -Name managedRuntimeVersion -Value ''   # "No Managed Code" - static site

New-Website -Name 'ops-status' -PhysicalPath 'C:\Sites\ops-status' `
    -ApplicationPool 'ops-status-pool' -IPAddress '*' -Port 80 -HostHeader 'status.asrs.lab'
```

Expected:

```
Name             ID   State      Physical Path                  Bindings
----             --   -----      -------------                  --------
ops-status       2    Started    C:\Sites\ops-status            http *:80:status.asrs.lab
```

Permissions: the pool runs as `IIS AppPool\ops-status-pool`, which is a member of `Users` at runtime; a new folder under `C:\` inherits `Users:(RX)`, so reading works without extra ACLs. If you put the site in a locked-down folder, grant `IIS AppPool\ops-status-pool:(OI)(CI)RX` with icacls.

`IISAdministration` equivalent: `New-IISSite -Name ops-status -PhysicalPath C:\Sites\ops-status -BindingInformation '*:80:status.asrs.lab'` (then set the pool via `Get-IISServerManager`).

## 6. Local tests

```powershell
(Invoke-WebRequest http://localhost/ -UseBasicParsing).Content
(Invoke-WebRequest http://localhost/ -UseBasicParsing -Headers @{ Host = 'status.asrs.lab' }).Content
```

```
<h1>ASRS default - EC2AMAZ-XXXXXXX</h1>
<h1>ops-status OK - EC2AMAZ-XXXXXXX</h1>
```

`-UseBasicParsing` avoids the Internet Explorer engine dependency in Windows PowerShell 5.1.

## 7. Test from Linux

Linux session (`aws ssm start-session --target <linux-id>`):

```bash
WIN_IP=10.20.2.57            # your value from step 3
curl -s http://$WIN_IP/
curl -s -H 'Host: status.asrs.lab' http://$WIN_IP/
curl -s --resolve status.asrs.lab:80:$WIN_IP http://status.asrs.lab/
curl -s -o /dev/null -w '%{http_code}\n' http://$WIN_IP/
```

```
<h1>ASRS default - EC2AMAZ-XXXXXXX</h1>
<h1>ops-status OK - EC2AMAZ-XXXXXXX</h1>
<h1>ops-status OK - EC2AMAZ-XXXXXXX</h1>
200
```

The request by bare IP has Host `10.20.2.57`, which matches no host header, so the Default Web Site (`*:80:` - no host) answers.

## 8. Stop the app pool

Windows:

```powershell
Stop-WebAppPool -Name 'ops-status-pool'
Get-WebAppPoolState -Name 'ops-status-pool'      # Value : Stopped
```

Linux:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: status.asrs.lab' http://$WIN_IP/   # 503
curl -s -o /dev/null -w '%{http_code}\n' http://$WIN_IP/                              # 200
curl -s -H 'Host: status.asrs.lab' http://$WIN_IP/
```

```
<h2>Service Unavailable</h2> ... HTTP Error 503. The service is unavailable.
```

The 503 comes from http.sys itself because the pool's request queue is disabled. Restore:

```powershell
Start-WebAppPool -Name 'ops-status-pool'
```

## 9. Logs

```powershell
Get-Website | Select-Object Name, Id, @{n='LogDir';e={$_.logFile.directory}}
Get-ChildItem C:\inetpub\logs\LogFiles -Recurse
```

```
Name             Id LogDir
----             -- ------
Default Web Site  1 %SystemDrive%\inetpub\logs\LogFiles
ops-status        2 %SystemDrive%\inetpub\logs\LogFiles

    Directory: C:\inetpub\logs\LogFiles\W3SVC1
-a----  u_ex261023.log
    Directory: C:\inetpub\logs\LogFiles\W3SVC2
-a----  u_ex261023.log
```

Folder `W3SVC<site id>`, one file per day, UTC timestamps, file name `u_exYYMMDD.log`.

Logs are buffered by http.sys and written roughly every minute or when the buffer fills. Force a flush:

```powershell
netsh http flush logbuffer
Get-Content C:\inetpub\logs\LogFiles\W3SVC2\u_ex*.log -Tail 5
```

```
#Fields: date time s-ip cs-method cs-uri-stem cs-uri-query s-port cs-username c-ip cs(User-Agent) cs(Referer) sc-status sc-substatus sc-win32-status time-taken
2026-10-23 14:12:09 10.20.2.57 GET / - 80 - 10.20.1.33 curl/8.5.0 - 200 0 0 3
```

`c-ip` 10.20.1.x is the Linux server. Note: the 503s from step 8 are NOT in the site log - http.sys rejected them before IIS processed them. They are in the http.sys error log:

```powershell
Get-Content C:\Windows\System32\LogFiles\HTTPERR\httperr*.log -Tail 5
```

```
2026-10-23 14:10:51 10.20.1.33 41022 10.20.2.57 80 HTTP/1.1 GET / 503 2 AppOffline ops-status-pool
```

## 10. Report for the ticket

```powershell
Get-Website | Select-Object Name, Id, State, PhysicalPath, ApplicationPool,
    @{n='Bindings';e={($_.bindings.Collection | ForEach-Object { "$($_.protocol) $($_.bindingInformation)" }) -join '; '}} |
    Format-List
Get-IISAppPool | Format-Table Name, State, ManagedRuntimeVersion, @{n='Identity';e={$_.ProcessModel.IdentityType}}
& "$env:windir\system32\inetsrv\appcmd.exe" list site
```

## Cleanup / reset

Keep IIS installed if you are continuing to exercises 04 and 10. To remove only this exercise:

```powershell
Remove-Website -Name 'ops-status'
Remove-WebAppPool -Name 'ops-status-pool'
Remove-Item C:\Sites -Recurse -Force
Remove-Item C:\inetpub\wwwroot\index.html
```

Full reset:

```powershell
Uninstall-WindowsFeature Web-Server -IncludeManagementTools
Restart-Computer -Force
# afterwards: Remove-Item C:\inetpub -Recurse -Force
```

## Common mistakes

- `New-Website` with port 80 and no host header while Default Web Site exists - the new site is created `Stopped` because of the binding conflict.
- Testing with a different port (8080) - IIS answers locally, but the security group only allows 80, so Linux times out.
- Expecting the 503 in the site's W3SVC log - check `HTTPERR`.
- Reading the log immediately and concluding the request never arrived - flush the log buffer first; timestamps are UTC.
- `Invoke-WebRequest` without `-UseBasicParsing` failing with an IE first-run error in an SSM session.
- Forgetting `Import-Module WebAdministration` before using the `IIS:` drive in a session started before the install.
- Calling the public IP from Linux - traffic goes out through the IGW and back; it is not allowed by the SGs. Always use the private IP inside the VPC.
