# AWS Lab 02 — Modify Security Groups and Validate Connectivity

## Objective

Open a new application port between the two lab servers using security-group references (not CIDRs), prove the path works end to end, explain which layer (security group, network ACL, OS firewall, listener) would block it if it failed, then revert and confirm the stack has no drift.

## Scenario

> **Change CHG-5520 (Standard):** The new inventory agent on `asrs-lab-linux` must call a service on `asrs-lab-windows` over **TCP 8080**. Security policy: no CIDR-based rules inside the VPC; reference security groups only; no inbound from the internet. Implement, test, attach evidence to the change, then back it out at the end of the test window (this is a rehearsal — the permanent change will go through CloudFormation).

## Prerequisites

- Stack `asrs-lab` deployed; both instances online in Session Manager (Lab 03 not in a broken state).
- Admin session `--profile asrs-admin`. Stack outputs `LinuxSecurityGroupId`, `WindowsSecurityGroupId`.
- Baseline rules: Linux SG <-> Windows SG allow ICMP echo and TCP 80 only (by SG reference). Egress 80/443 to `0.0.0.0/0`. Nothing listens on 8080 yet.
- Session Manager plugin locally. One shell on each instance.

## What you need to accomplish

1. Record the current inbound and outbound rules of both security groups (evidence "before").
2. Start a temporary listener on TCP 8080 on the Windows server and make sure the Windows OS firewall would allow it.
3. From Linux, prove 8080 is blocked **before** your change and note what failure looks like (timeout vs. refused).
4. Add the minimum security-group rules so Linux can reach Windows on 8080. Think about **both** directions of rule evaluation on **both** groups; explain why no rule is needed for return traffic.
5. Prove the connection succeeds from Linux (TCP-level test and, optionally, an HTTP request).
6. Prove the network path with a tool that analyses configuration rather than sending packets, and know what it costs.
7. Explain how you would tell an SG block from a NACL block from a Windows Firewall block from "nothing listening", using only evidence.
8. Revert: remove only the rules you added, stop the listener, remove any OS firewall rule you created, and run drift detection on the Security stack.

## Commands or tools to research

- `aws ec2 describe-security-group-rules`, `authorize-security-group-ingress`, `authorize-security-group-egress`, `revoke-security-group-ingress`, `revoke-security-group-egress` (`--ip-permissions` with `UserIdGroupPairs`, `--security-group-rule-ids`)
- `aws ec2 describe-network-acls`
- PowerShell: `Test-NetConnection`, `Get-NetTCPConnection`, `New-NetFirewallRule`, `System.Net.Sockets.TcpListener`
- Bash: `curl`, `/dev/tcp`, `timeout`, `ss`
- VPC Reachability Analyzer: `create-network-insights-path`, `start-network-insights-analysis`, `describe-network-insights-analyses`
- VPC Flow Logs (`create-flow-logs`) — optional
- `aws cloudformation detect-stack-drift`

## Validation criteria

- Before: Linux -> Windows 8080 **times out** (SG drop), not "connection refused".
- After: TCP connect to Windows private IP on 8080 succeeds from Linux; `Get-NetTCPConnection -LocalPort 8080` on Windows shows the established connection from the Linux private IP.
- Rules reference the peer security group ID, not an IP/CIDR. No rule allows 8080 from `0.0.0.0/0`.
- Reachability Analyzer reports the path as reachable (if you ran it) and names the SG rules used.
- After revert: rule sets match the "before" evidence exactly; drift status `IN_SYNC`.

## Recovery considerations

- Console or CLI edits to the SGs are **drift**. Depending on how the template declares rules, the next `deploy.yml` may remove your rules silently or leave them unmanaged. Always revert, then `detect-stack-drift` on the Security nested stack.
- Revoke by **rule ID** to avoid deleting a template-managed rule by accident. Do not touch the 443 egress rule (that breaks SSM — Lab 03).
- Reachability Analyzer is billed per analysis (about USD 0.10 each). Delete paths/analyses afterwards.
- Flow Logs are optional; they cost CloudWatch Logs ingestion/storage. If you enable them, delete the flow log and the log group.
- A PowerShell listener started in an SSM session dies when the session ends — that is fine, but remove any firewall rule you added.

## Interview angle

"Security groups are stateful and allow-only, evaluated per ENI; NACLs are stateless, ordered, allow/deny, per subnet. A timeout usually means a drop (SG/NACL/host firewall); 'connection refused' means the packet reached a host with nothing listening."

[Solution](solutions/aws-02-security-groups.md) — try it first.
