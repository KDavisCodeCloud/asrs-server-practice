# Solution — AWS Lab 02: Modify Security Groups and Validate Connectivity

Conventions: local commands use `--profile asrs-admin --region us-east-1`.

## 0. Gather IDs

```bash
out() { aws cloudformation describe-stacks --stack-name asrs-lab \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text \
  --profile asrs-admin --region us-east-1; }
LINUX_ID=$(out LinuxInstanceId); WIN_ID=$(out WindowsInstanceId)
LINUX_SG=$(out LinuxSecurityGroupId); WIN_SG=$(out WindowsSecurityGroupId)
WIN_IP=$(aws ec2 describe-instances --instance-ids "$WIN_ID" \
  --query "Reservations[0].Instances[0].PrivateIpAddress" --output text --profile asrs-admin --region us-east-1)
echo "$LINUX_SG $WIN_SG $WIN_IP"
```

## 1. Evidence "before"

```bash
for SG in "$LINUX_SG" "$WIN_SG"; do
  aws ec2 describe-security-group-rules --filters Name=group-id,Values="$SG" \
    --query "SecurityGroupRules[].[GroupId,IsEgress,IpProtocol,FromPort,ToPort,CidrIpv4,ReferencedGroupInfo.GroupId,SecurityGroupRuleId]" \
    --output table --profile asrs-admin --region us-east-1
done | tee sg-before.txt
```
Expected shape: Windows SG ingress `icmp` and `tcp 80` from `$LINUX_SG`; Linux SG egress `tcp 80`, `tcp 443` to `0.0.0.0/0` (plus whatever SG-referenced egress the template defines for ICMP/80). Note: Linux egress to 8080 is **not** allowed — that matters in step 4.

Console: VPC > Security groups > select group > Inbound / Outbound rules.

## 2. Listener on Windows

```bash
aws ssm start-session --target "$WIN_ID" --profile asrs-admin --region us-east-1
```
The session opens `cmd`/PowerShell; run `powershell` if needed:

```powershell
New-NetFirewallRule -DisplayName "LAB02-8080" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow
$l = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, 8080)
$l.Start()
Get-NetTCPConnection -LocalPort 8080 -State Listen     # LocalAddress 0.0.0.0, State Listen
```
Leave this session open (the listener lives in this PowerShell process). Windows Firewall blocks unsolicited inbound by default; without the rule you would get a timeout even with correct SGs — the OS-firewall layer.

## 3. Prove it is blocked (Linux session)

```bash
aws ssm start-session --target "$LINUX_ID" --profile asrs-admin --region us-east-1
```
```bash
WIN_IP=10.20.2.x   # from step 0
timeout 5 bash -c "</dev/tcp/$WIN_IP/8080" && echo OPEN || echo "BLOCKED/TIMEOUT rc=$?"
# BLOCKED/TIMEOUT rc=124   -> timeout = packets dropped
timeout 5 bash -c "</dev/tcp/$WIN_IP/80" && echo OPEN || echo "rc=$?"
# port 80 is allowed by SG but nothing listens -> fast "Connection refused" (or timeout if Windows Firewall drops it)
```
`rc=124` (timeout) = silently dropped. "Connection refused" = reached the host, nothing listening (a TCP RST).

## 4. Add the minimum rules

Two checks happen for a new connection Linux -> Windows:
- **Linux SG egress** must allow tcp/8080 to the destination (egress is only 80/443 today).
- **Windows SG ingress** must allow tcp/8080 from the source.

Return traffic needs no rule in either SG because SGs are stateful (connection tracking).

```bash
aws ec2 authorize-security-group-ingress --group-id "$WIN_SG" \
  --ip-permissions "IpProtocol=tcp,FromPort=8080,ToPort=8080,UserIdGroupPairs=[{GroupId=$LINUX_SG,Description='LAB02 inventory agent'}]" \
  --tag-specifications 'ResourceType=security-group-rule,Tags=[{Key=Lab,Value=lab02}]' \
  --query "SecurityGroupRules[0].SecurityGroupRuleId" --output text --profile asrs-admin --region us-east-1
# sgr-0aaa...  -> save as IN_RULE

aws ec2 authorize-security-group-egress --group-id "$LINUX_SG" \
  --ip-permissions "IpProtocol=tcp,FromPort=8080,ToPort=8080,UserIdGroupPairs=[{GroupId=$WIN_SG,Description='LAB02 inventory agent'}]" \
  --tag-specifications 'ResourceType=security-group-rule,Tags=[{Key=Lab,Value=lab02}]' \
  --query "SecurityGroupRules[0].SecurityGroupRuleId" --output text --profile asrs-admin --region us-east-1
# sgr-0bbb...  -> save as EG_RULE
```
Console alternative: Windows SG > Edit inbound rules > Add rule > Custom TCP 8080 > Source: the Linux SG ID (type `sg-` and pick it).

## 5. Prove it works

Linux:
```bash
timeout 5 bash -c "</dev/tcp/$WIN_IP/8080" && echo OPEN
# OPEN
curl -sv --max-time 5 http://$WIN_IP:8080/ 2>&1 | head -5
# "Connected to 10.20.2.x port 8080" then it waits (raw TCP listener, not HTTP) -> TCP path proven
```
Windows (second session, or before `AcceptTcpClient`):
```powershell
$c = $l.AcceptTcpClient(); $c.Client.RemoteEndPoint   # 10.20.1.x:<ephemeral>
```
Reverse direction check (optional), from Windows: `Test-NetConnection 10.20.1.x -Port 80` shows `TcpTestSucceeded : False` unless something listens on Linux port 80 — useful to show "allowed but nothing listening".

## 6. Reachability Analyzer (config analysis, ~USD 0.10 per analysis)

```bash
PATH_ID=$(aws ec2 create-network-insights-path --source "$LINUX_ID" --destination "$WIN_ID" \
  --protocol tcp --destination-port 8080 \
  --tag-specifications 'ResourceType=network-insights-path,Tags=[{Key=Project,Value=asrs-lab}]' \
  --query NetworkInsightsPath.NetworkInsightsPathId --output text --profile asrs-admin --region us-east-1)
AN_ID=$(aws ec2 start-network-insights-analysis --network-insights-path-id "$PATH_ID" \
  --query NetworkInsightsAnalysis.NetworkInsightsAnalysisId --output text --profile asrs-admin --region us-east-1)
sleep 30
aws ec2 describe-network-insights-analyses --network-insights-analysis-ids "$AN_ID" \
  --query "NetworkInsightsAnalyses[0].[Status,NetworkPathFound]" --profile asrs-admin --region us-east-1
# ["succeeded", true]
```
If `false`, `Explanations[]` names the blocking component (e.g. `ExplanationCode: ENI_SG_RULES_MISMATCH`). It does **not** see the OS firewall or whether anything listens — it only analyses AWS configuration.

## 7. Layer-by-layer diagnosis

| Symptom / evidence | Layer |
|---|---|
| Timeout; Reachability Analyzer says SG rule missing | Security group (stateful; allow-only; per ENI) |
| Timeout; analyzer says NACL; `describe-network-acls` shows DENY or missing ephemeral-port return rule | NACL (stateless; numbered rules; per subnet; needs explicit return traffic 1024-65535) |
| Analyzer reachable, still timeout; Flow Log shows `ACCEPT` at the ENI | Host firewall (Windows Firewall / firewalld) |
| Immediate "Connection refused" | Packet arrived; no listener (`Get-NetTCPConnection` / `ss -ltn`) |

Check NACLs: `aws ec2 describe-network-acls --filters Name=vpc-id,Values=<vpc-id> --query "NetworkAcls[].Entries"`. The lab is expected to use default NACLs (allow all) — verify rather than assume.

Optional Flow Logs (CloudWatch ingestion cost): `aws ec2 create-flow-logs --resource-type NetworkInterface --resource-ids <eni> --traffic-type REJECT ...` needs a log group and an IAM delivery role; `REJECT` records on 8080 before the fix confirm an SG/NACL drop. Delete the flow log and log group afterwards.

## 8. Revert and drift check

```bash
aws ec2 revoke-security-group-ingress --group-id "$WIN_SG" --security-group-rule-ids "$IN_RULE" --profile asrs-admin --region us-east-1
aws ec2 revoke-security-group-egress  --group-id "$LINUX_SG" --security-group-rule-ids "$EG_RULE" --profile asrs-admin --region us-east-1
aws ec2 delete-network-insights-analysis --network-insights-analysis-id "$AN_ID" --profile asrs-admin --region us-east-1
aws ec2 delete-network-insights-path --network-insights-path-id "$PATH_ID" --profile asrs-admin --region us-east-1
```
Windows session: `$l.Stop(); Remove-NetFirewallRule -DisplayName "LAB02-8080"`.

Re-run step 1 into `sg-after.txt` and `diff sg-before.txt sg-after.txt` (no output expected; rule IDs of template rules are unchanged).

```bash
SEC_STACK=$(aws cloudformation describe-stack-resources --stack-name asrs-lab \
  --query "StackResources[?ResourceType=='AWS::CloudFormation::Stack' && contains(LogicalResourceId,'Security')].PhysicalResourceId" \
  --output text --profile asrs-admin --region us-east-1)
DID=$(aws cloudformation detect-stack-drift --stack-name "$SEC_STACK" --query StackDriftDetectionId --output text --profile asrs-admin --region us-east-1)
sleep 20
aws cloudformation describe-stack-drift-detection-status --stack-drift-detection-id "$DID" \
  --query "[DetectionStatus,StackDriftStatus]" --profile asrs-admin --region us-east-1
# ["DETECTION_COMPLETE","IN_SYNC"]
```
Tip: run drift detection once **before** reverting to see what out-of-band changes look like (`MODIFIED` on the SG with `PropertyDifferences` under `SecurityGroupIngress`/`SecurityGroupEgress`). Rules declared as separate `AWS::EC2::SecurityGroupIngress` resources may not report extra rules as drift — another reason not to rely on drift detection alone.

## Common mistakes

- Adding only the Windows ingress rule and forgetting the Linux **egress** rule (egress here is restricted, not the default allow-all).
- Using `10.20.1.0/24` as the source instead of the SG reference (violates the change policy; also breaks if the subnet changes).
- Adding inbound rules on the Linux SG "for the return traffic" — unnecessary with stateful SGs.
- Forgetting Windows Firewall, then blaming the SG.
- Testing with `ping` and concluding TCP works (ICMP and TCP are separate rules; Windows also blocks ICMP echo by default unless the firewall rule is enabled).
- Revoking with `--ip-permissions` that accidentally matches a template-managed rule. Use rule IDs.
- Leaving Reachability Analyzer paths/analyses or Flow Logs behind.
