# Solution — AWS Lab 03: Troubleshoot SSM Session Manager Access

Conventions: local commands use `--profile asrs-admin --region us-east-1`.

## 0. Gather IDs and record baseline

```bash
out() { aws cloudformation describe-stacks --stack-name asrs-lab \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text \
  --profile asrs-admin --region us-east-1; }
LINUX_ID=$(out LinuxInstanceId); LINUX_SG=$(out LinuxSecurityGroupId)
PROFILE_ARN=$(aws ec2 describe-instances --instance-ids "$LINUX_ID" \
  --query "Reservations[0].Instances[0].IamInstanceProfile.Arn" --output text --profile asrs-admin --region us-east-1)
ROLE_NAME=$(aws iam get-instance-profile --instance-profile-name "${PROFILE_ARN##*/}" \
  --query "InstanceProfile.Roles[0].RoleName" --output text --profile asrs-admin)

aws ssm describe-instance-information --filters Key=InstanceIds,Values="$LINUX_ID" \
  --query "InstanceInformationList[0].[PingStatus,LastPingDateTime,AgentVersion,PlatformName]" \
  --profile asrs-admin --region us-east-1
# ["Online", "2026-10-23T...", "3.3.x", "Amazon Linux"]
```

## 1. Inject the fault (pick one)

```bash
# Fault A — IAM
aws iam detach-role-policy --role-name "$ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore --profile asrs-admin

# Fault B — Network (record the rule ID first)
RULE_443=$(aws ec2 describe-security-group-rules --filters Name=group-id,Values="$LINUX_SG" \
  --query "SecurityGroupRules[?IsEgress && FromPort==\`443\` && CidrIpv4=='0.0.0.0/0'].SecurityGroupRuleId" \
  --output text --profile asrs-admin --region us-east-1)
aws ec2 revoke-security-group-egress --group-id "$LINUX_SG" --security-group-rule-ids "$RULE_443" \
  --profile asrs-admin --region us-east-1

# Fault C — Agent (inside an SSM session on Linux; your session will drop)
sudo systemctl disable --now amazon-ssm-agent
```

## 2. Symptom

```bash
aws ssm start-session --target "$LINUX_ID" --profile asrs-admin --region us-east-1
```
```
An error occurred (TargetNotConnected) when calling the StartSession operation: i-0abc... is not connected.
```
With Fault A/B it may take a few minutes before the status flips; an already-open session can keep working until the websocket drops.

## 3. Registration state

```bash
aws ssm describe-instance-information --filters Key=InstanceIds,Values="$LINUX_ID" \
  --query "InstanceInformationList[0].[PingStatus,LastPingDateTime]" --profile asrs-admin --region us-east-1
# ["ConnectionLost", "2026-10-23T14:02:11..."]
```
Console: Systems Manager > Fleet Manager (or Managed nodes) > node shows **Connection lost**; EC2 > instance > Status checks show 2/2 passed — the OS is fine, the management channel is not. If the instance never appears in the list at all, the agent has never registered (usually IAM).

## 4. Rule out each prerequisite from outside

**a. IAM**
```bash
aws iam list-attached-role-policies --role-name "$ROLE_NAME" --profile asrs-admin
# Fault A: AttachedPolicies: []   -> agent cannot call ssm:UpdateInstanceInformation / ssmmessages:*
aws ec2 describe-instances --instance-ids "$LINUX_ID" \
  --query "Reservations[0].Instances[0].IamInstanceProfile" --profile asrs-admin --region us-east-1
# Instance profile still attached (a missing profile is another common variant)
```

**b. Network (443 egress, endpoints, DNS)**
```bash
aws ec2 describe-security-group-rules --filters Name=group-id,Values="$LINUX_SG" \
  --query "SecurityGroupRules[?IsEgress].[IpProtocol,FromPort,ToPort,CidrIpv4,ReferencedGroupInfo.GroupId]" \
  --output table --profile asrs-admin --region us-east-1
# Fault B: no tcp 443 to 0.0.0.0/0

VPC_ID=$(aws ec2 describe-instances --instance-ids "$LINUX_ID" --query "Reservations[0].Instances[0].VpcId" --output text --profile asrs-admin --region us-east-1)
aws ec2 describe-vpc-endpoints --filters Name=vpc-id,Values="$VPC_ID" \
  --query "VpcEndpoints[].[ServiceName,VpcEndpointType,PrivateDnsEnabled,State]" --output table --profile asrs-admin --region us-east-1
# Default: only com.amazonaws.us-east-1.s3 (Gateway). With EnableSsmVpcEndpoints=true you'd also see ssm, ssmmessages, ec2messages (Interface).
aws ec2 describe-vpc-attribute --vpc-id "$VPC_ID" --attribute enableDnsSupport --profile asrs-admin --region us-east-1
aws ec2 describe-vpc-attribute --vpc-id "$VPC_ID" --attribute enableDnsHostnames --profile asrs-admin --region us-east-1
aws ec2 describe-instances --instance-ids "$LINUX_ID" --query "Reservations[0].Instances[0].PublicIpAddress" --output text --profile asrs-admin --region us-east-1
```
Without NAT and without interface endpoints the instance needs a public IP + IGW route to reach the public SSM endpoints. With interface endpoints, the endpoint SG must allow 443 from the instance SG, and private DNS must be enabled (both VPC DNS attributes `true`).

**c. Agent / OS** — you cannot see inside without SSM, but:
```bash
aws ec2 get-console-output --instance-id "$LINUX_ID" --latest --output text --profile asrs-admin --region us-east-1 | tail -30
```
Useful after a reboot (boot messages, agent start). For Fault C the agent simply stops checking in while IAM and network look perfect — that combination points to the agent.

Agent logs (when you do have a shell — e.g. via serial console or on the other instance for comparison):
- Linux: `sudo tail -50 /var/log/amazon/ssm/amazon-ssm-agent.log` and `errors.log`
  - Fault A: `AccessDeniedException ... not authorized to perform: ssm:UpdateInstanceInformation` or `no valid credentials could be retrieved for ec2 identity`
  - Fault B: `dial tcp ...:443: i/o timeout` to `ssm.us-east-1.amazonaws.com`
- Windows: `Get-Content "$env:ProgramData\Amazon\SSM\Logs\amazon-ssm-agent.log" -Tail 50`

**d. IMDS** — agent gets credentials from IMDSv2. From a working shell: `TOKEN=$(curl -sX PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 60"); curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/iam/security-credentials/` — empty/404 means no role reachable. From outside: `aws ec2 describe-instances ... --query "Reservations[0].Instances[0].MetadataOptions"` (`HttpEndpoint: enabled`, `HttpTokens: required`, `HttpPutResponseHopLimit`).

## 5. Automation runbook

```bash
EXEC_ID=$(aws ssm start-automation-execution --document-name AWSSupport-TroubleshootManagedInstance \
  --parameters "InstanceId=$LINUX_ID" --query AutomationExecutionId --output text \
  --profile asrs-admin --region us-east-1)
sleep 60
aws ssm get-automation-execution --automation-execution-id "$EXEC_ID" \
  --query "AutomationExecution.[AutomationExecutionStatus,Outputs]" --profile asrs-admin --region us-east-1
```
It checks (from the AWS side) VPC endpoints, SG/NACL rules for 443, route to IGW/NAT, DNS attributes, instance profile and its SSM permissions. Typical output for Fault B: security group does not allow outbound 443 to the SSM endpoints; for Fault A: instance profile role lacks required SSM permissions. It cannot see inside the OS, so for Fault C it reports everything as OK — which is itself the clue.
Console: Systems Manager > Automation > Execute automation > search `AWSSupport-TroubleshootManagedInstance`.

## 6. Restore

```bash
# Fault A
aws iam attach-role-policy --role-name "$ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore --profile asrs-admin

# Fault B
aws ec2 authorize-security-group-egress --group-id "$LINUX_SG" \
  --ip-permissions 'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}]' \
  --profile asrs-admin --region us-east-1
```
Use the same description the template uses if it sets one (check `sg-before` evidence from Lab 02), otherwise drift detection will flag the rule as `MODIFIED`.

The agent retries with backoff; to speed it up, reboot:
```bash
aws ec2 reboot-instances --instance-ids "$LINUX_ID" --profile asrs-admin --region us-east-1
```

**Fault C** — no AWS-side fix restarts a disabled service. Options:

1. **EC2 Serial Console** (Nitro instances, t3 supported):
   ```bash
   aws ec2 enable-serial-console-access --profile asrs-admin --region us-east-1     # account+Region setting
   ```
   Requires an OS user with a **password** (AL2023 users have none by default — it must be set in advance, which is why you do it before you need it) or GRUB single-user access. Connect via EC2 console > instance > Connect > EC2 serial console, log in, `sudo systemctl enable --now amazon-ssm-agent`. Disable afterwards: `aws ec2 disable-serial-console-access`. Windows uses SAC (Special Admin Console) and needs EMS/SAC enabled in the BCD in advance.
2. **Stop/start or reboot**: does not help for a *disabled* service; helps if you only `stop`ped it (it starts at boot because it is enabled).
3. **User data**: could re-enable the agent with a `#cloud-boothook`/cloud-config that runs every boot, but this lab does not use user data and editing it on the instance outside CloudFormation is drift; mention, don't use.
4. **Rescue instance**: stop, detach root volume, attach to a helper instance, re-enable the unit (`ln -s` into `multi-user.target.wants`), reattach. Heavy; last resort.
5. Replace the instance via CloudFormation (data on the root volume is lost) — acceptable in a lab, not in production.

For this lab, the simplest practice of Fault C is `sudo systemctl stop amazon-ssm-agent` (not `disable`) followed by `aws ec2 reboot-instances`.

## 7. Validate

```bash
aws ssm describe-instance-information --filters Key=InstanceIds,Values="$LINUX_ID" \
  --query "InstanceInformationList[0].[PingStatus,LastPingDateTime]" --profile asrs-admin --region us-east-1
# ["Online", "<now>"]
aws ssm start-session --target "$LINUX_ID" --profile asrs-admin --region us-east-1
sudo systemctl is-active amazon-ssm-agent; sudo systemctl is-enabled amazon-ssm-agent   # active / enabled
```
Repeat `start-session` against `WindowsInstanceId`.

Drift check on the nested stack you touched (Iam for A, Security for B; C leaves no CloudFormation drift):
```bash
NESTED=$(aws cloudformation describe-stack-resources --stack-name asrs-lab \
  --query "StackResources[?ResourceType=='AWS::CloudFormation::Stack' && contains(LogicalResourceId,'Iam')].PhysicalResourceId" \
  --output text --profile asrs-admin --region us-east-1)
DID=$(aws cloudformation detect-stack-drift --stack-name "$NESTED" --query StackDriftDetectionId --output text --profile asrs-admin --region us-east-1)
sleep 20
aws cloudformation describe-stack-resource-drifts --stack-name "$NESTED" \
  --stack-resource-drift-status-filters MODIFIED DELETED \
  --query "StackResourceDrifts[].[LogicalResourceId,StackResourceDriftStatus]" --profile asrs-admin --region us-east-1
# [] when IN_SYNC
```
Run it once while the fault is active to see what drift looks like (e.g. role `MODIFIED`, `ManagedPolicyArns` difference).

About "just redeploy": running `deploy.yml` with an unchanged template returns "No updates are to be performed" and **does not** re-attach the policy or re-add the rule. CloudFormation reconciles only resources it updates. If you want CloudFormation to repair it, make the deploy touch that resource (e.g. a real template change) — or fix by hand and prove with drift detection, as above.

## Cleanup / repeat

- Ensure serial console access is disabled again if you enabled it.
- `aws ec2 describe-security-group-rules` and `list-attached-role-policies` match the baseline.
- Repeat with another fault, or with the Windows instance (agent service: `Stop-Service AmazonSSMAgent; Set-Service AmazonSSMAgent -StartupType Disabled`).

## Common mistakes

- Waiting on Fleet Manager status immediately after injecting — it lags; use `LastPingDateTime`.
- Re-attaching the policy and expecting instant recovery; the agent backs off — reboot or wait.
- Re-adding 443 egress with a different description and then seeing drift.
- Assuming the S3 gateway endpoint helps SSM — it does not; SSM needs interface endpoints or internet egress.
- Enabling interface endpoints but forgetting endpoint SG ingress 443 from the instance SG, or private DNS.
- Believing a no-change redeploy repairs drift.
- Leaving serial console access enabled.
