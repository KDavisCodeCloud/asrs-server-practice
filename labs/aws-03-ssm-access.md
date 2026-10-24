# AWS Lab 03 — Troubleshoot SSM Session Manager Access

## Objective

Break Session Manager access to one lab instance in a controlled way, diagnose the cause from the outside using only AWS-side evidence, restore access, and return the stack to an `IN_SYNC` state. Know the out-of-band recovery options when SSM is the only door and it is closed.

## Scenario

> **Ticket INC-4502 (P2):** Operations cannot connect to `asrs-lab-linux` from Session Manager or Fleet Manager. The console shows the instance as running and passing status checks, but `start-session` returns `TargetNotConnected`. No SSH key or inbound port exists by design. Restore access and identify root cause.

## Prerequisites

- Stack `asrs-lab` deployed; both instances currently **Online** in Fleet Manager.
- Admin session `--profile asrs-admin`; Session Manager plugin installed locally.
- Pick **one** fault to inject (do it yourself, ideally have someone else pick so you diagnose blind). Use the Linux instance first; repeat on Windows later.
  - **Fault A — IAM:** detach `AmazonSSMManagedInstanceCore` from the Linux instance role.
  - **Fault B — Network:** revoke the TCP 443 egress rule (`0.0.0.0/0`) from the Linux security group.
  - **Fault C — Agent:** in a session, stop (and disable) the `amazon-ssm-agent` service.
- Note: with Fault C you cut off your own shell; for A and B the existing session may survive briefly, new ones will fail.
- Fault-injection changes are **drift**. Record exactly what you changed.

## What you need to accomplish

1. Confirm the symptom: a new session fails, and capture the error.
2. Determine the instance's SSM registration state and when it last checked in, from the CLI and from Fleet Manager.
3. Systematically rule in/out the four prerequisites of Session Manager: agent running, IAM credentials via instance profile, network path to the SSM endpoints on 443 (including DNS), and IMDS reachable. State which evidence you used for each.
4. Run the AWS-managed troubleshooting automation runbook against the instance and interpret its output.
5. Restore access without SSH and without replacing the instance.
6. Explain how you would get a console on the instance if SSM could not be repaired from the outside (Fault C with no other option), including the prerequisites of that method.
7. Verify the stack is back to its template-defined state and that a new session works on both instances.

## Commands or tools to research

- `aws ssm start-session`, `aws ssm describe-instance-information` (`PingStatus`, `LastPingDateTime`, `AgentVersion`)
- Fleet Manager / Systems Manager > Managed nodes; EC2 status checks; `aws ec2 get-console-output`
- `aws ssm start-automation-execution` with `AWSSupport-TroubleshootManagedInstance`; `get-automation-execution`
- `aws iam list-attached-role-policies`, `attach-role-policy`
- `aws ec2 describe-security-group-rules`, `authorize-security-group-egress`
- `aws ec2 describe-vpc-endpoints`, `describe-vpc-attribute` (DNS support/hostnames)
- Agent log on Linux: `/var/log/amazon/ssm/amazon-ssm-agent.log`; Windows: `%PROGRAMDATA%\Amazon\SSM\Logs\amazon-ssm-agent.log`
- EC2 Serial Console (`enable-serial-console-access`, `ec2-instance-connect send-serial-console-ssh-public-key`), stop/start
- `aws cloudformation detect-stack-drift`

## Validation criteria

- You named the fault from evidence before looking at what was changed, and you can cite the specific evidence (e.g. `PingStatus: ConnectionLost` + role has no SSM policy; or runbook step showing 443 to `ssm.us-east-1.amazonaws.com` failing).
- `describe-instance-information` shows `PingStatus: Online` again with a fresh `LastPingDateTime`.
- `start-session` succeeds on both instances.
- Instance role has exactly `AmazonSSMManagedInstanceCore`; Linux SG egress is back to 80/443; agent is `active (running)` and `enabled`.
- Drift detection on the affected nested stack(s) reports `IN_SYNC`.

## Recovery considerations

- **Redeploying does not automatically fix drift.** CloudFormation only touches resources whose template properties changed; a no-op `deploy.yml` run ("No updates are to be performed") leaves a detached policy detached. Restore the property by hand to match the template, or make a real template change, then confirm with drift detection.
- If a later deploy *does* update the drifted resource, it usually overwrites your manual state — that is the fix here, but it is also how hand-made "temporary" fixes disappear.
- After restoring IAM, the agent can take several minutes to pick up new credentials; restarting the agent or a reboot speeds it up.
- Serial console access is an **account-level** setting and needs an OS password; leaving it enabled widens the attack surface — disable it again.
- Stop/start changes the public IP (no EIP in this lab) and the host; data on gp3 volumes is preserved.
- Optional SSM interface endpoints (`EnableSsmVpcEndpoints=true`) cost per endpoint-hour per AZ; leave the default `false` unless you are testing the private path.
- Cost of leftovers: Automation runbook executions are free for AWS-owned runbooks within normal limits.

## Interview angle

"Session Manager needs four things: a running agent, credentials from the instance profile with the SSM core permissions, outbound 443 to `ssm`, `ssmmessages` and `ec2messages` (via internet/NAT or interface endpoints with private DNS), and IMDS. Check them in that order, starting from `PingStatus`."

[Solution](solutions/aws-03-ssm-access.md) — try it first.
