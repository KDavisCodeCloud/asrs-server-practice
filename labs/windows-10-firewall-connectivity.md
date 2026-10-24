# Windows 10 - Windows Firewall and connectivity

## Objective

Troubleshoot connectivity between two EC2 instances by separating the layers - security groups (AWS) versus Windows Defender Firewall (host) versus the listening service - and manage host firewall rules precisely from PowerShell.

## Scenario

> **Ticket INC-3355** - Priority 2
> The monitoring server `asrs-lab-linux` reports `asrs-lab-windows` as DOWN: ping fails and the HTTP check on port 80 times out. The Windows admin insists "the security group allows ICMP and port 80 from the Linux server, so it must be AWS". Someone also asked to open SSH (22) from the Windows box to the Linux box "for troubleshooting". Find out which layer blocks what, fix ping and HTTP with least-privilege host rules (Linux subnet only), and explain why the SSH request will not work without an AWS change.

## Prerequisites

- Exercise 03 completed (IIS serving on port 80). If you skipped it, the solution shows a temporary PowerShell listener you can use instead.
- SSM sessions on both servers. Note both private IPs (Windows in 10.20.2.0/24, Linux in 10.20.1.0/24).
- Read-only access to the security groups via the AWS CLI on your workstation (`ec2:DescribeSecurityGroups`, `ec2:DescribeInstances`).

### Set up the fault (you do this yourself on Windows)

1. Leave ICMP echo blocked - that is the Windows default; do not enable anything yet.
2. Back up the firewall policy, then add a rule that silently blocks HTTP:
   ```powershell
   New-Item -ItemType Directory C:\LabBackup -Force | Out-Null
   netsh advfirewall export C:\LabBackup\firewall-before.wfw
   New-NetFirewallRule -DisplayName 'zz-temp-maintenance' -Direction Inbound -Protocol TCP -LocalPort 80 -Action Block -Profile Any | Out-Null
   ```
3. Forget the rule name.

## What you need to accomplish

1. From Linux, reproduce both symptoms and note whether each failure is a timeout or an immediate refusal.
2. From the AWS API, document exactly what the Windows and Linux security groups allow inbound and outbound, and conclude whether the SG permits ICMP echo and TCP 80 from Linux to Windows.
3. On Windows, determine the active network profile and the per-profile firewall state, default actions and logging settings.
4. Prove whether the Linux packets reach the Windows host at all, using host-side evidence (enable firewall drop logging and/or look at connection state), so the blame is assigned by evidence.
5. Find every enabled inbound rule that affects TCP 80, including blocks, and explain block-versus-allow precedence. Find and remove/disable the offending rule.
6. Allow ICMPv4 echo request inbound from the Linux subnet only (either by scoping the built-in rule or with a new rule), and verify ping from Linux.
7. Make sure HTTP 80 is allowed only from 10.20.1.0/24 and verify with curl from Linux.
8. From Windows, test outbound: TCP 80 to the Linux server, ICMP to the Linux server, TCP 22 to the Linux server, and TCP 443 to the internet. Explain each result by layer.
9. Explain why a host firewall change can never fix the SSH request and what would need to change in AWS (do not change the SGs).
10. Restore the original firewall configuration from your export (then re-apply only your intended rules if continuing).

## Commands or tools to research

- `Get-NetConnectionProfile`, `Get-NetFirewallProfile`, `Set-NetFirewallProfile` (`-LogBlocked`, `-LogAllowed`, `-LogFileName`)
- `Get-NetFirewallRule` (`-Direction`, `-Enabled`, `-Action`, `-DisplayGroup`, `-PolicyStore ActiveStore`), `Get-NetFirewallPortFilter`, `Get-NetFirewallAddressFilter`, `Show-NetFirewallRule`
- `New-NetFirewallRule` (`-Protocol ICMPv4 -IcmpType 8`, `-RemoteAddress`), `Set-NetFirewallRule`, `Enable-NetFirewallRule`, `Disable-NetFirewallRule`, `Remove-NetFirewallRule`
- `netsh advfirewall export` / `import`, `netsh advfirewall firewall show rule`
- `Test-NetConnection` (`-Port`, `-InformationLevel Detailed`), `Get-NetTCPConnection`, `pktmon` (built-in packet monitor)
- Linux: `ping`, `curl -v -m`, `nc -zv`, `ss -ltn`, `python3 -m http.server`
- `aws ec2 describe-security-groups`, VPC Flow Logs (concept), Reachability Analyzer (concept)

## Validation criteria

- From Linux: `ping -c 3 <win-ip>` gets replies and `curl -s -o /dev/null -w '%{http_code}' http://<win-ip>/` returns 200.
- `Get-NetFirewallRule` shows your ICMP and HTTP allow rules with `RemoteAddress 10.20.1.0/24` and no enabled inbound block rule for port 80.
- Firewall log evidence: the blocked HTTP SYNs from 10.20.1.x appear as `DROP TCP ... 80` before the fix and not after.
- From Windows: `Test-NetConnection <linux-ip> -Port 80` succeeds only while something listens on Linux:80; `-Port 22` fails; `Test-NetConnection aws.amazon.com -Port 443` succeeds.
- You can state for each test which layer allowed or blocked it.

## Recovery considerations

- Session Manager depends on **outbound** HTTPS (443) from the SSM agent to AWS endpoints. Never set the default outbound action to Block or add an outbound block for 443 - you will lose SSM, and with no inbound ports there is no other way in.
- If you do lose SSM because of the firewall: Run Command will not work either (same agent channel). Recovery is offline - stop the instance and run `AWSSupport-ExecuteEC2Rescue` (EC2Rescue offline can reset common firewall/remote-access settings), or detach the root volume, attach it to a rescue Windows instance and edit the firewall policy in the offline `SYSTEM` hive. Snapshot before starting this exercise.
- `netsh advfirewall import` replaces the entire policy with the exported one; keep the export file until you are done.
- Disabling a whole profile (`Set-NetFirewallProfile -Enabled False`) "fixes" the ticket but is not an acceptable answer - use scoped rules.
- Firewall logging to a file grows up to its size limit; turn `LogBlocked` back off when finished.

## Interview angle

"Ping fails but the security group allows ICMP - what next?" - SGs are stateful and evaluated before the packet reaches the OS; if the SG and NACL allow it, check the host firewall (Windows blocks echo by default), then the service. Timeout = dropped somewhere (SG, NACL, host firewall); connection refused = reached the host, nothing listening. Prove it with host firewall logs, VPC Flow Logs (REJECT vs ACCEPT) or Reachability Analyzer.

[Solution](solutions/windows-10-firewall-connectivity.md) — try it first.
