# Solution — Linux 10: Troubleshoot networking and SSM connectivity

Examples use `us-east-1`. Replace with your region.

## 1. Network inventory

```bash
ip -br addr
# lo      UNKNOWN  127.0.0.1/8 ::1/128
# ens5    UP       10.20.1.25/24 fe80::.../64

ip route
# default via 10.20.1.1 dev ens5 proto dhcp src 10.20.1.25 metric 512
# 10.20.0.2 via 10.20.1.1 dev ens5 proto dhcp src 10.20.1.25 metric 512
# 10.20.1.0/24 dev ens5 proto kernel scope link src 10.20.1.25 metric 512
# 10.20.1.1 dev ens5 proto dhcp scope link src 10.20.1.25 metric 512

ip neigh          # ARP cache: gateway 10.20.1.1 should be REACHABLE/STALE
```

- `.1` of the subnet is the VPC router; the Internet Gateway is reached through it via the
  route table. The public IPv4 is NAT'd by the IGW and never appears on `ens5`.
- AL2023 uses systemd-networkd and systemd-resolved:

```bash
networkctl status ens5 --no-pager | head -20
resolvectl status ens5
# Current DNS Server: 10.20.0.2
cat /etc/resolv.conf
# nameserver 127.0.0.53     (local resolved stub, forwards to 10.20.0.2)
```

`10.20.0.2` is the Amazon-provided VPC resolver (VPC CIDR base + 2).

Listening sockets:

```bash
sudo ss -tulpn
# udp UNCONN 127.0.0.53%lo:53   systemd-resolved
# udp UNCONN 127.0.0.1:323      chronyd
# tcp LISTEN *:80               httpd    (only if you did Linux 03)
```

The SSM agent does **not** listen on any port: it makes outbound HTTPS connections only.
That is why no inbound SG rule is needed.

## 2. SSM endpoint reachability

| Endpoint | Purpose |
|---|---|
| `ssm.<region>.amazonaws.com` | Registration, inventory, Run Command API calls |
| `ssmmessages.<region>.amazonaws.com` | Session Manager data channel (WebSocket) |
| `ec2messages.<region>.amazonaws.com` | Agent message polling (Run Command delivery) on older/compat paths |

```bash
TOKEN=$(curl -sS -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')
REGION=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)

for svc in ssm ssmmessages ec2messages; do
  h=$svc.$REGION.amazonaws.com
  ip=$(getent hosts $h | awk '{print $1; exit}')
  code=$(curl -sS -o /dev/null --connect-timeout 5 -w '%{http_code}' https://$h/ 2>&1)
  echo "$h  ip=$ip  http=$code"
done
```

Expected:

```
ssm.us-east-1.amazonaws.com  ip=52.46.x.x  http=404
ssmmessages.us-east-1.amazonaws.com  ip=3.x.x.x  http=404
ec2messages.us-east-1.amazonaws.com  ip=52.94.x.x  http=404
```

Any HTTP status code (4xx is normal for a bare GET) proves DNS, routing, SG egress and TLS all
work. A timeout (`curl: (28) Connection timed out`) or `000` means a network block; a resolve
error (`Could not resolve host`) means DNS.

If `EnableSsmVpcEndpoints=true`, the names resolve to **private** `10.20.x.x` addresses
(interface endpoints with private DNS), and traffic never leaves the VPC.

TLS detail when needed:

```bash
openssl s_client -connect ssm.$REGION.amazonaws.com:443 -servername ssm.$REGION.amazonaws.com </dev/null 2>/dev/null | grep -E 'subject=|Verify return'
timeout 5 bash -c "</dev/tcp/ssmmessages.$REGION.amazonaws.com/443" && echo tcp-ok
```

## 3. IMDSv2 and credentials

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://169.254.169.254/latest/meta-data/
# 401     <- IMDSv1 (no token) rejected: IMDSv2 required

curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/iam/info
# { "Code" : "Success", "InstanceProfileArn" : "arn:aws:iam::123456789012:instance-profile/asrs-lab-...", ... }

ROLE=$(curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/iam/security-credentials/)
curl -sS -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/iam/security-credentials/$ROLE \
  | grep -E '"Code"|"Expiration"'
# "Code" : "Success",
# "Expiration" : "2026-10-23T18:12:40Z"
```

Do not print the full credential document into shared logs.

`aws s3 ls` from the instance (AWS CLI v2 is on AL2023):

```
An error occurred (AccessDenied) when calling the ListBuckets operation: ...
```

Correct: the role only has `AmazonSSMManagedInstanceCore`, which grants SSM/ssmmessages/
ec2messages actions and nothing for S3 listing. Least privilege working as designed.

## 4. Agent health

```bash
systemctl status amazon-ssm-agent --no-pager | head -5
#      Active: active (running) since ...
rpm -q amazon-ssm-agent
sudo tail -n 30 /var/log/amazon/ssm/amazon-ssm-agent.log
sudo grep -iE 'error|timeout' /var/log/amazon/ssm/errors.log | tail
```

Healthy indicators in `amazon-ssm-agent.log`:

```
INFO [MessageService] [MGSInteractor] Starting MGS interactor
INFO [MessageGatewayService] Successfully opened websocket connection to: wss://ssmmessages.us-east-1.amazonaws.com/v1/control-channel/i-0123...
INFO [HealthCheck] HealthCheck reporting agent health.
```

A line containing `Entering SSM Agent hibernate` means the agent repeatedly failed to
register (usually no credentials or no route) and is backing off; it should be absent.

Built-in diagnostics (recent agent versions):

```bash
sudo ssm-cli get-diagnostics --output table
```

It checks IMDS, credentials, DNS and TCP to each endpoint, proxy settings, and agent version,
and prints Success/Failed per check.

## 5. Induced outage — diagnose from outside

Save and break (workstation):

```bash
SG=sg-0linux...          # LinuxSecurityGroupId
IID=i-0123456789abcdef0
aws ec2 describe-security-groups --group-ids $SG --query 'SecurityGroups[0].IpPermissionsEgress' > linux-sg-egress.json

aws ec2 revoke-security-group-egress --group-id $SG \
  --ip-permissions 'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}]'
```

Revoke matches on protocol, ports and CIDR; the rule's description does not need to be supplied.

Observe:

```bash
watch -n 30 "aws ssm describe-instance-information --filters Key=InstanceIds,Values=$IID \
  --query 'InstanceInformationList[0].[PingStatus,LastPingDateTime]' --output text"
# Online   2026-10-23T12:01:10   ... then after several minutes:
# ConnectionLost  2026-10-23T12:01:10

aws ssm start-session --target $IID
# An error occurred (TargetNotConnected) when calling the StartSession operation: i-0123... is not connected.
```

Existing sessions usually freeze or drop. Run Command also fails (stays `Pending`/`Undeliverable`).

Systematic outside-in checks:

```bash
# Instance state and status checks
aws ec2 describe-instance-status --instance-ids $IID \
  --query 'InstanceStatuses[0].[InstanceState.Name,SystemStatus.Status,InstanceStatus.Status]'
# ["running","ok","ok"]   -> OS is up; problem is reachability, not the instance

# Security group egress
aws ec2 describe-security-groups --group-ids $SG \
  --query 'SecurityGroups[0].IpPermissionsEgress[].[IpProtocol,FromPort,ToPort,IpRanges[].CidrIp,UserIdGroupPairs[].GroupId]'
# 443 to 0.0.0.0/0 is missing   <- root cause

# Route table for the subnet
SUBNET=$(aws ec2 describe-instances --instance-ids $IID --query 'Reservations[0].Instances[0].SubnetId' --output text)
aws ec2 describe-route-tables --filters Name=association.subnet-id,Values=$SUBNET \
  --query 'RouteTables[0].Routes[].[DestinationCidrBlock,GatewayId,State]'
# [["10.20.0.0/16","local","active"],["0.0.0.0/0","igw-...","active"]]

# NACL (stateless: needs ephemeral return ports inbound)
aws ec2 describe-network-acls --filters Name=association.subnet-id,Values=$SUBNET \
  --query 'NetworkAcls[0].Entries[].[Egress,RuleNumber,Protocol,RuleAction,CidrBlock,PortRange]'

# Public IP present (no NAT gateway, so outbound needs one)
aws ec2 describe-instances --instance-ids $IID --query 'Reservations[0].Instances[0].PublicIpAddress'
```

Checklist order for "Connection lost": instance running and status checks → SG egress 443 →
route to IGW (or NAT / VPC endpoints) → NACL → public IP (for IGW path) → DNS (VPC
`enableDnsSupport`) → instance profile attached and role has `AmazonSSMManagedInstanceCore` →
IMDS reachable → agent running (only checkable with console screenshot/serial or after reboot).

Root cause: SG egress 443 removed; the agent cannot reach the SSM endpoints. Restore:

```bash
aws ec2 authorize-security-group-egress --group-id $SG \
  --ip-permissions 'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0,Description=<original description if any>}]'
aws ec2 describe-security-groups --group-ids $SG --query 'SecurityGroups[0].IpPermissionsEgress' | diff - linux-sg-egress.json && echo "egress restored exactly"
```

Ping status returns to `Online` within a few minutes.

## 6. Agent evidence of the outage

After reconnecting:

```bash
sudo grep -E '2026-10-23 12:0[2-9]' /var/log/amazon/ssm/errors.log | head
```

Typical lines:

```
ERROR [MessageGatewayService] Failed to get controlchannel token, error: ... dial tcp 3.x.x.x:443: i/o timeout
ERROR [ssm-agent-worker] [MessagingDeliveryService] error when calling AWS APIs. error details - RequestError: send request failed ... i/o timeout
```

`i/o timeout` (not `connection refused`, not `no such host`) points at a silent drop — a
security group or NACL — rather than DNS or the service.

## 7. East-west to Windows

```bash
WIN=10.20.2.40      # WindowsPrivateIp
ping -c 3 -W 2 $WIN
curl -sS -o /dev/null --connect-timeout 5 -w '%{http_code}\n' http://$WIN/
```

Results and meaning:

| State | ping | curl :80 | Why |
|---|---|---|---|
| Before IIS, default Windows Firewall | 100% loss | `curl: (28) Connection timed out` / `000` | SGs allow both, but Windows Firewall drops inbound ICMPv4 echo and unsolicited TCP 80 by default. Silent drop = timeout. |
| IIS installed | 100% loss | `200` | Installing IIS enables the "World Wide Web Services (HTTP Traffic-In)" firewall rule. ICMP is still blocked. |
| Echo rule enabled on Windows (`Enable-NetFirewallRule -Name FPS-ICMP4-ERQ-In`) | replies | `200` | Both layers allow. |
| IIS stopped (`Stop-Service W3SVC`), firewall rule still on | replies | `curl: (7) Failed to connect ... Connection refused` | Host reachable; nothing listening. RST = refused. |

Interpretation rule: **timeout** = something is dropping (SG, NACL, host firewall);
**refused** = reachable host, no listener; **HTTP status** = application layer, read the code.

SG check confirms the AWS layer is not the cause:

```bash
aws ec2 describe-security-groups --group-ids <WindowsSecurityGroupId> \
  --query 'SecurityGroups[0].IpPermissions[].[IpProtocol,FromPort,ToPort,UserIdGroupPairs[].GroupId]'
# [["icmp",8,-1,["sg-0linux..."]],["tcp",80,80,["sg-0linux..."]]]
```

`tracepath -n $WIN` shows a single hop: both subnets are in one VPC and use the `local` route.

## Cleanup / reset

- Confirm the egress rule is restored (`diff` above) and the instance is `Online`.
- Optional: run CloudFormation drift detection on the security group nested stack; it should
  report `IN_SYNC`.
- `rm linux-sg-egress.json` on the workstation.
- On Windows, disable the ICMP rule again if you enabled it, to return to the default state.

## Common mistakes

- Looking for an inbound rule to "allow SSM" — the agent is outbound-only.
- Assuming `ping` failing means the network is down; Windows blocks echo by default and many
  AWS endpoints do not answer ICMP at all. Test the actual port.
- Calling IMDS without a token and concluding IMDS is broken (401 is the correct IMDSv2 response).
- Forgetting the region in the endpoint names (`ssm.amazonaws.com` is not the endpoint the agent uses).
- Restoring the SG rule with a different CIDR or without the original description, causing drift.
- Restarting the agent from inside a session during troubleshooting and losing access.
- Treating `AccessDenied` on `aws s3 ls` from the instance as a fault.
