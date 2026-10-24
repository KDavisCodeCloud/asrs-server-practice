# Windows 03 - Install and administer IIS

## Objective

Install the IIS web server role from PowerShell, host a second site with its own application pool and binding, read the IIS logs, and prove the site works from another server in the VPC.

## Scenario

> **Ticket CHG-4410** - Standard change
> The operations team wants an internal status page on `asrs-lab-windows`. Install IIS. Keep the Default Web Site serving a simple "ASRS default" page. Add a new site `ops-status` served from `C:\Sites\ops-status`, running in its own application pool `ops-status-pool`, answering only for host name `status.asrs.lab` on port 80 (the security groups only allow port 80 between the lab servers - you cannot use another port). The Linux server `asrs-lab-linux` is the monitoring host; it must be able to fetch both pages by private IP. Provide the log location so the monitoring team can see their requests.

## Prerequisites

- SSM sessions to both `asrs-lab-windows` (PowerShell) and `asrs-lab-linux` (shell; `aws ssm start-session --target <linux-instance-id>`).
- Outbound 443 from Windows (allowed by the SG) is needed only if the feature payload has to be fetched; Server 2022 AMIs normally have it locally.
- Expect `Install-WindowsFeature` to take several minutes on a t3.small (2 GiB RAM). Don't interrupt it.

## What you need to accomplish

1. Confirm IIS is not installed, then install the Web Server role with its management tools (PowerShell module and IIS Manager).
2. Confirm the `W3SVC` and `WAS` services are running and something is listening on TCP 80.
3. Find the Windows server's private IPv4 address from inside the OS.
4. Replace the default page with content identifying the server (`ASRS default - <hostname>`).
5. Create `C:\Sites\ops-status\index.html`, an application pool `ops-status-pool`, and a site `ops-status` bound to `http` / all IPs / port 80 / host header `status.asrs.lab`.
6. Verify both sites locally from Windows.
7. From the Linux server, fetch the default site and the `ops-status` site by the Windows private IP (you must make the host header work without DNS).
8. Stop the `ops-status` application pool and observe what the Linux client receives; start it again.
9. Find the IIS log for each site, identify the Linux server's requests (client IP, status code) and explain why entries can appear late.
10. List the sites, bindings and app pools in one report suitable for the ticket.

## Commands or tools to research

- `Get-WindowsFeature`, `Install-WindowsFeature` (`-IncludeManagementTools`), `Uninstall-WindowsFeature`
- Modules `WebAdministration` (`IIS:` drive, `New-WebAppPool`, `New-Website`, `Get-WebBinding`, `New-WebBinding`, `Start-WebAppPool`, `Stop-WebAppPool`, `Get-WebAppPoolState`) and `IISAdministration` (`Get-IISSite`, `Get-IISAppPool`)
- `appcmd.exe` (`%windir%\system32\inetsrv`)
- `Get-NetTCPConnection`, `Get-NetIPAddress`, IMDSv2 `local-ipv4`
- `Invoke-WebRequest` (`-UseBasicParsing`, `-Headers`)
- `curl -H "Host: ..."`, `curl --resolve` (Linux)
- IIS W3C logs, `netsh http flush logbuffer`, `iisreset`

## Validation criteria

- `Get-WindowsFeature Web-Server` shows `[X]` / `Installed`.
- `Get-Service W3SVC, WAS` both `Running`; `Get-NetTCPConnection -LocalPort 80 -State Listen` returns a listener (owning process `System` - http.sys).
- From Linux: `curl http://<win-private-ip>/` returns `ASRS default - ...`; the same request with host `status.asrs.lab` returns the ops-status page.
- With `ops-status-pool` stopped, Linux receives HTTP 503 for the ops-status host and still 200 for the default site.
- The log files under `C:\inetpub\logs\LogFiles\W3SVC<id>\` contain the Linux server's private IP (10.20.1.x) with status 200 and 503.

## Recovery considerations

- Installing IIS creates and enables the inbound firewall rule "World Wide Web Services (HTTP Traffic-In)". If Linux times out, check that rule (exercise 10) before blaming IIS.
- If the install hangs or fails with low memory, reboot (`Restart-Computer`), reconnect via SSM after 2-3 minutes and retry. Watch `CBS.log` (`C:\Windows\Logs\CBS\CBS.log`).
- A site that fails to start with "The process cannot access the file because it is being used by another process" means a binding conflict (same IP:port:host as another site).
- Rollback: remove the site and pool, or `Uninstall-WindowsFeature Web-Server -IncludeManagementTools` followed by a reboot. Exercises 04 and 10 reuse IIS, so leave it installed unless you are resetting.
- Keep backups of IIS config before big changes: `Backup-WebConfiguration` / `appcmd add backup`.

## Interview angle

"Two sites on the same server, same IP, same port - how?" - Host-header bindings; IIS (http.sys) routes by the Host header, and a site with no host header acts as the catch-all. A 503 usually means the app pool is stopped or crashing (check rapid-fail protection and the System log for WAS events).

[Solution](solutions/windows-03-iis.md) — try it first.
