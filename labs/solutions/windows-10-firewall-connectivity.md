# Solution: Windows 10 - Windows Firewall and connectivity

Variables used below - set them in each shell:

```
WIN_IP=10.20.2.57      # Windows private IP (exercise 03, step 3)
LIN_IP=10.20.1.33      # Linux private IP: `hostname -I` on Linux
```

## 1. Reproduce from Linux

```bash
ping -c 3 -W 2 $WIN_IP
curl -v -m 5 http://$WIN_IP/
```

```
3 packets transmitted, 0 received, 100% packet loss, time 2050ms

*   Trying 10.20.2.57:80...
* Connection timed out after 5001 milliseconds
curl: (28) Connection timed out after 5001 milliseconds
```

Both are **timeouts** (silent drops), not "Connection refused". A drop can come from the SG, the network ACL, or the host firewall (Windows Firewall drops silently - it does not send RST or ICMP unreachable). So far nothing proves it is AWS.

## 2. What the security groups allow

Workstation:

```bash
for NAME in asrs-lab-windows asrs-lab-linux; do
  ID=$(aws ec2 describe-instances --filters Name=tag:Name,Values=$NAME Name=instance-state-name,Values=running \
       --query 'Reservations[0].Instances[0].InstanceId' --output text)
  for SG in $(aws ec2 describe-instances --instance-ids $ID --query 'Reservations[0].Instances[0].SecurityGroups[].GroupId' --output text); do
    echo "== $NAME $SG"
    aws ec2 describe-security-groups --group-ids $SG --query 'SecurityGroups[0].{
      In: IpPermissions[].[IpProtocol,FromPort,ToPort,join(`,`,IpRanges[].CidrIp),join(`,`,UserIdGroupPairs[].GroupId)],
      Out: IpPermissionsEgress[].[IpProtocol,FromPort,ToPort,join(`,`,IpRanges[].CidrIp),join(`,`,UserIdGroupPairs[].GroupId)]}' --output json
  done
done
```

Summary (matches the lab design):

| SG | Inbound | Outbound |
|---|---|---|
| Windows SG | ICMP echo (type 8) and TCP 80 from Linux SG | TCP 80, 443 to 0.0.0.0/0; TCP 80 and ICMP to Linux SG |
| Linux SG | ICMP echo and TCP 80 from Windows SG | TCP 80, 443 to 0.0.0.0/0; TCP 80 and ICMP to Windows SG |

For Linux -> Windows ping and HTTP: Linux egress allows them, Windows ingress allows them, and SGs are stateful (replies are allowed automatically). The default VPC network ACL allows all. So the SG is **not** the blocker - but prove it from the host side (step 4).

## 3. Profiles and firewall state (Windows)

```powershell
Get-NetConnectionProfile | Select-Object InterfaceAlias, Name, NetworkCategory
Get-NetFirewallProfile | Format-Table Name, Enabled, DefaultInboundAction, DefaultOutboundAction, LogBlocked, LogAllowed, LogFileName -AutoSize
```

```
InterfaceAlias Name          NetworkCategory
-------------- ----          ---------------
Ethernet       Network 2     Public

Name    Enabled DefaultInboundAction DefaultOutboundAction LogBlocked LogAllowed LogFileName
----    ------- -------------------- --------------------- ---------- ---------- -----------
Domain     True        NotConfigured         NotConfigured      False      False %systemroot%\system32\LogFiles\Firewall\pfirewall.log
Private    True        NotConfigured         NotConfigured      False      False ...
Public     True        NotConfigured         NotConfigured      False      False ...
```

`NotConfigured` means the built-in default: inbound **Block**, outbound **Allow**. The active profile is the one matching `NetworkCategory` (normally `Public` on a standalone EC2 instance; Domain only when domain-joined). Rules created with `-Profile Any` apply regardless.

## 4. Prove the packets reach the host

Enable drop logging and repeat the Linux tests:

```powershell
Set-NetFirewallProfile -All -LogBlocked True -LogMaxSizeKilobytes 4096
```

Linux: `ping -c 3 -W 2 $WIN_IP; curl -m 5 http://$WIN_IP/`

Windows (wait ~1 minute for the log to flush):

```powershell
Get-Content "$env:SystemRoot\System32\LogFiles\Firewall\pfirewall.log" -Tail 20 | Select-String '10\.20\.1\.'
```

```
2026-10-23 17:40:01 DROP ICMP 10.20.1.33 10.20.2.57 - - 84 - - - - 8 0 - RECEIVE
2026-10-23 17:40:04 DROP TCP 10.20.1.33 10.20.2.57 41022 80 60 S 3550012 0 62727 - - - RECEIVE
```

The packets arrived at the Windows network stack and **Windows Firewall dropped them** (ICMP type 8 and TCP SYN to port 80). If the SG or NACL had dropped them, nothing would appear here at all - in AWS you would then look at VPC Flow Logs (`REJECT` records) or run Reachability Analyzer. Here the AWS layer is cleared by evidence.

Supporting evidence - IIS is listening, so this is not a service problem:

```powershell
Get-NetTCPConnection -LocalPort 80 -State Listen
Get-Service W3SVC
(Invoke-WebRequest http://localhost/ -UseBasicParsing).StatusCode      # 200 locally
```

Optional packet-level view with the built-in packet monitor:

```powershell
pktmon filter add LabHTTP -t TCP -p 80
pktmon start --capture
# run curl from Linux, then:
pktmon stop
pktmon format PktMon.etl -o C:\LabBackup\pktmon.txt
pktmon filter remove
```

## 5. Rules affecting TCP 80 and precedence

```powershell
Get-NetFirewallPortFilter -Protocol TCP |
    Where-Object { $_.LocalPort -contains '80' -or $_.LocalPort -contains 'Any' } |
    Get-NetFirewallRule |
    Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' } |
    Select-Object Name, DisplayName, Action, Profile,
        @{n='RemoteAddress';e={ ($_ | Get-NetFirewallAddressFilter).RemoteAddress -join ',' }} |
    Format-Table -AutoSize
```

```
Name                              DisplayName                                Action Profile RemoteAddress
----                              -----------                                ------ ------- -------------
IIS-WebServerRole-HTTP-In-TCP     World Wide Web Services (HTTP Traffic-In)   Allow     Any Any
{6f1c...}                         zz-temp-maintenance                         Block     Any Any
```

Precedence in Windows Firewall is not "first match" like an ACL list: an applicable **Block rule wins over any Allow rule** (only "allow if secure / override block rules" IPsec-authenticated rules beat it), and the default action applies only when no rule matches. That is why the IIS allow rule did nothing.

Remove the offending rule (record it in the ticket first):

```powershell
Get-NetFirewallRule -DisplayName 'zz-temp-maintenance' | Format-List Name, DisplayName, Action, Direction, Enabled
Remove-NetFirewallRule -DisplayName 'zz-temp-maintenance'
```

(`Disable-NetFirewallRule` is the gentler choice if you are unsure who created it.)

## 6. Allow ICMPv4 echo from the Linux subnet only

Preferred - a dedicated, clearly named rule:

```powershell
New-NetFirewallRule -Name 'LAB-ICMP4-Echo-In' -DisplayName 'LAB Allow ICMPv4 echo from Linux subnet' `
    -Direction Inbound -Protocol ICMPv4 -IcmpType 8 -RemoteAddress 10.20.1.0/24 -Action Allow -Profile Any
```

Alternative - scope and enable the built-in rule:

```powershell
Get-NetFirewallRule -DisplayName 'File and Printer Sharing (Echo Request - ICMPv4-In)' |
    Set-NetFirewallRule -RemoteAddress 10.20.1.0/24 -Enabled True
```

Linux:

```bash
ping -c 3 $WIN_IP
```

```
64 bytes from 10.20.2.57: icmp_seq=1 ttl=128 time=0.41 ms
3 packets transmitted, 3 received, 0% packet loss
```

(TTL 128 is the Windows default - a quick OS fingerprint.)

## 7. HTTP from the Linux subnet only

If IIS is installed, scope its rule instead of adding a duplicate:

```powershell
Set-NetFirewallRule -Name 'IIS-WebServerRole-HTTP-In-TCP' -RemoteAddress 10.20.1.0/24
Get-NetFirewallRule -Name 'IIS-WebServerRole-HTTP-In-TCP' | Get-NetFirewallAddressFilter | Select-Object RemoteAddress
```

Without IIS, create the rule and start a temporary listener in a second SSM session:

```powershell
New-NetFirewallRule -Name 'LAB-HTTP-In' -DisplayName 'LAB Allow HTTP from Linux subnet' `
    -Direction Inbound -Protocol TCP -LocalPort 80 -RemoteAddress 10.20.1.0/24 -Action Allow -Profile Any

$l = New-Object System.Net.HttpListener; $l.Prefixes.Add('http://+:80/'); $l.Start()
while ($l.IsListening) {
    $ctx = $l.GetContext(); $b = [Text.Encoding]::UTF8.GetBytes("lab listener $env:COMPUTERNAME")
    $ctx.Response.OutputStream.Write($b, 0, $b.Length); $ctx.Response.Close()
}   # end with: terminate that SSM session
```

Linux:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://$WIN_IP/      # 200
```

## 8. Outbound tests from Windows

Helper that distinguishes timeout from refusal:

```powershell
function Test-Tcp($HostName, $Port, $TimeoutMs = 3000) {
    $c = New-Object System.Net.Sockets.TcpClient
    try {
        $t = $c.ConnectAsync($HostName, $Port)
        if (-not $t.Wait($TimeoutMs)) { return "$HostName`:$Port TIMEOUT (dropped - SG/NACL/firewall)" }
        "$HostName`:$Port OPEN"
    } catch {
        "$HostName`:$Port REFUSED/ERROR - $($_.Exception.InnerException.Message)"
    } finally { $c.Dispose() }
}
```

Run the tests (first without a listener on Linux, then with `sudo python3 -m http.server 80` running in the Linux session):

```powershell
Test-Tcp 10.20.1.33 80                              # use your Linux private IP
Test-NetConnection 10.20.1.33                       # ICMP
Test-Tcp 10.20.1.33 22
Test-NetConnection aws.amazon.com -Port 443 | Select-Object ComputerName, RemoteAddress, TcpTestSucceeded
```

| Test | Result | Why |
|---|---|---|
| TCP 80 -> Linux, no listener | REFUSED ("actively refused") | Windows egress SG allows 80 to Linux SG, Linux ingress SG allows 80 from Windows SG, packet reaches Linux, nothing listens -> RST. AL2023 has no host firewall enabled by default. |
| TCP 80 -> Linux, `http.server` running | OPEN | Same path, now a listener. |
| ICMP -> Linux | `PingSucceeded : True` | ICMP allowed both SGs; Linux answers echo. Windows outbound default is Allow. |
| TCP 22 -> Linux | TIMEOUT | Windows SG **egress** has no rule for 22, and Linux SG has no **ingress** 22. Dropped by AWS before reaching Linux - even though `sshd` is listening on Linux. |
| TCP 443 -> internet | `TcpTestSucceeded : True` | Egress 443 to 0.0.0.0/0, public IP + IGW. This is also the path SSM uses. |

`Test-NetConnection -Port` only says True/False (and takes ~20 s on a drop, because of retransmissions), so the helper is more useful for "timeout vs refused".

## 9. Why the SSH request needs an AWS change

Windows Firewall already allows all outbound traffic by default, so no host rule on Windows is involved. Both security groups block it: the Windows SG has no egress rule for TCP 22 and the Linux SG has no ingress rule for TCP 22. Fixing it would require two SG changes (Windows SG egress tcp/22 to the Linux SG, Linux SG ingress tcp/22 from the Windows SG) through the CloudFormation stack, not by hand, to avoid drift. The better answer in the ticket: decline - administrative access to Linux is via Session Manager, which needs no inbound port at all, is IAM-controlled and logged.

## 10. Restore

```powershell
netsh advfirewall import C:\LabBackup\firewall-before.wfw
Get-NetFirewallRule -DisplayName 'zz-temp-maintenance' -ErrorAction SilentlyContinue      # gone (it was added after the export)
Get-NetFirewallRule -Name 'LAB-*' -ErrorAction SilentlyContinue                           # gone
```

`import` replaces the whole policy, so it reverts everything after the export - including logging settings and your intended rules. If you are continuing with the lab, re-run steps 6-7 afterwards.

## Cleanup / reset

```powershell
Set-NetFirewallProfile -All -LogBlocked False
Remove-NetFirewallRule -Name 'LAB-ICMP4-Echo-In', 'LAB-HTTP-In' -ErrorAction SilentlyContinue
Set-NetFirewallRule -Name 'IIS-WebServerRole-HTTP-In-TCP' -RemoteAddress Any -ErrorAction SilentlyContinue
Remove-Item C:\LabBackup\firewall-before.wfw, C:\LabBackup\pktmon.txt, .\PktMon.etl -ErrorAction SilentlyContinue
```

Stop `python3 -m http.server` on Linux with Ctrl+C. Repeat the exercise by redoing the fault setup.

## Common mistakes

- Concluding "it's the SG" from a timeout - a host firewall drop also looks like a timeout. Use the firewall log or Flow Logs to decide.
- Disabling the firewall profile to make it work.
- Enabling the built-in ICMP rule without scoping it (it then answers anyone the SG lets through - defence in depth says scope both layers).
- Adding a second allow rule for 80 when a block rule exists - block wins; find the block.
- Testing Windows -> Linux 80 with no Linux listener and reading "refused" as "firewall blocked" - refused means the path is open.
- Creating rules for the wrong profile (`-Profile Domain` on a non-domain machine).
- Blocking outbound traffic broadly while "hardening" and cutting off the SSM agent's 443 to AWS.
- Pinging the public IP from Linux - SG rules reference the peer SG, which only matches private-IP traffic inside the VPC.
