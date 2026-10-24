# Linux 10 — Troubleshoot networking and SSM connectivity

## Objective

Map the server's network configuration, verify each dependency Session Manager needs
(DNS, route, security group egress, IMDS credentials, agent health), diagnose an SSM
outage from outside the instance, and test east-west connectivity to the Windows server.

## Scenario

> **Ticket INC-2130** — `asrs-lab-linux` intermittently shows **Connection lost** in Fleet
> Manager and engineers cannot start sessions. There is no SSH or bastion: SSM is the only
> way in. Separately, the Windows team says the Linux server "can't reach" their IIS site on
> `asrs-lab-windows`. Produce a connectivity runbook for this server, reproduce the SSM
> outage in a controlled way, and determine whether the IIS issue is network, firewall, or
> application.

## Prerequisites

- Session on `asrs-lab-linux` with sudo.
- AWS CLI on your workstation with EC2 read, `ec2:RevokeSecurityGroupEgress` /
  `ec2:AuthorizeSecurityGroupEgress` on the lab SG, and SSM read permissions.
- Stack outputs: `LinuxInstanceId`, `LinuxSecurityGroupId`, `WindowsPrivateIp`.
- For task 7: IIS installed on `asrs-lab-windows` (the Windows exercises cover this). Do the
  ping/curl tests once before IIS exists and once after.

**Setup (controlled SSM outage, from your workstation only):**

1. Note the exact egress rules on `LinuxSecurityGroupId` (save the JSON).
2. Remove the egress rule that allows TCP 443 to `0.0.0.0/0`.
3. Watch the instance's SSM ping status over the next several minutes and try to start a new session.
4. Leave it broken while you complete task 5, then restore the rule exactly.

## What you need to accomplish

1. Document the instance's IP address, subnet prefix, default gateway, DNS resolver and
   listening sockets.
2. Prove, from the instance, that it can resolve and open TLS connections to the three
   regional endpoints Session Manager depends on — and explain what each is for.
3. Prove IMDSv2 is enforced and that the instance profile's temporary credentials are being
   issued. Explain why `aws s3 ls` from the instance fails and why that is correct.
4. Check the SSM agent's service state, version, and logs; find the log lines that show a
   successful registration/connection.
5. During the induced outage, diagnose it entirely from outside: SSM ping status, the
   security group rules, the route table and the subnet's NACL. State the root cause and
   restore service.
6. Find the agent log evidence of the outage after you reconnect.
7. Test ICMP and HTTP to the Windows private IP. Explain each result: timeout vs refused vs
   success, and which layer (SG, Windows Firewall, IIS) caused it.

## Commands or tools to research

- `ip addr`, `ip route`, `ip neigh`, `ss -tulpn`, `networkctl`, `resolvectl`, `/etc/resolv.conf`
- `getent hosts`, `curl` (`-v`, `-sS`, `-o /dev/null -w '%{http_code}'`, `--connect-timeout`),
  `openssl s_client`, bash `/dev/tcp`, `ping`, `tracepath`
- IMDSv2: token `PUT` to `169.254.169.254/latest/api/token`, metadata paths
  `placement/region`, `iam/info`, `iam/security-credentials/`
- `systemctl status amazon-ssm-agent`, `/var/log/amazon/ssm/amazon-ssm-agent.log`,
  `/var/log/amazon/ssm/errors.log`, `ssm-cli get-diagnostics`
- `aws ssm describe-instance-information`, `aws ec2 describe-security-groups`,
  `describe-route-tables`, `describe-network-acls`, `revoke-security-group-egress`,
  `authorize-security-group-egress`
- VPC resolver address (VPC CIDR base + 2)

## Validation criteria

- Runbook lists IP `10.20.1.x/24`, default route via `10.20.1.1`, resolver behaviour, and
  listening ports (no inbound services expected besides anything you added).
- Each of `ssm`, `ssmmessages`, `ec2messages` endpoints resolves and completes a TLS handshake.
- IMDSv1 request returns `401`; IMDSv2 returns the role name and a credential `Expiration`.
- `aws ssm describe-instance-information` shows `ConnectionLost` during the outage and `Online`
  after restoring the rule; the SG egress rules match your saved JSON exactly.
- Agent log contains errors from the outage window (timeouts to the endpoints).
- Ping/HTTP results to Windows are recorded with an explanation for each.

## Recovery considerations

- Revoking egress 443 cuts off SSM, Run Command and package repositories at once — you
  cannot fix it from inside the instance. Only the workstation CLI/console can restore it.
- Restore the rule exactly as saved (protocol, port range, CIDR, description) or the stack
  drifts. If unsure, a stack update via `deploy.yml` re-applies the template's SG rules.
- The agent retries with backoff; after restoring egress, Online can take a few minutes.
  If it stays offline, stop/start the instance via the stop-start workflow.
- Do not stop or restart `amazon-ssm-agent` from your own session, and do not add `iptables`
  or `nftables` rules that block 443 or `169.254.169.254` — both lock you out until a stop/start.
- Do not "fix" the IIS test by opening port 80 or ICMP to `0.0.0.0/0`.

**Interview angle:** "An instance shows Connection lost in Systems Manager — what do you check,
in order, when you cannot log in to it?"

[Solution](solutions/linux-10-networking-ssm.md) — try it first.
