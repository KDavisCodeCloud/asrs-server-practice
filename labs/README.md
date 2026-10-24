# Hands-on exercises

25 exercises: 10 Linux, 10 Windows, 5 AWS. Each guide tells you **what** to achieve and **how
you will know it worked**, not how to do it. Full solutions are in `solutions/`. Attempt the
exercise first.

Before you start: deploy the lab, confirm both servers are **Online** in Systems Manager, and know
how to connect (`docs/ACCESS.md`). Snapshot before risky disk work (`aws-04`).

## Linux (Amazon Linux 2023, `asrs-lab-linux`)

| # | Exercise | Skills |
|---|---|---|
| 01 | [Users and sudo](linux-01-users-and-sudo.md) | useradd, groups, sudoers, visudo |
| 02 | [Permissions and ownership](linux-02-permissions-ownership.md) | chmod, chown, setgid, umask, ACLs |
| 03 | [Apache](linux-03-apache.md) | dnf, systemctl, httpd config, logs |
| 04 | [Failed systemd service](linux-04-failed-systemd-service.md) | systemctl status, journalctl, unit files |
| 05 | [Security updates](linux-05-security-updates.md) | dnf updateinfo, AL2023 release versions, reboots |
| 06 | [Cron jobs](linux-06-cron-jobs.md) | cronie, crontab, systemd timers |
| 07 | [CPU and memory](linux-07-cpu-memory.md) | top, ps, free, vmstat, OOM, T3 credits |
| 08 | [Disk utilisation](linux-08-disk-utilization.md) | df, du, lsof, journald, inodes |
| 09 | [Expand EBS and filesystem](linux-09-expand-ebs-filesystem.md) | Modify Volume, growpart, xfs_growfs |
| 10 | [Networking and SSM connectivity](linux-10-networking-ssm.md) | ip, ss, curl, SSM agent logs |

## Windows (Windows Server 2022, `asrs-lab-windows`)

| # | Exercise | Skills |
|---|---|---|
| 01 | [Local users and administrators](windows-01-local-users-admins.md) | New-LocalUser, local groups |
| 02 | [NTFS permissions](windows-02-ntfs-permissions.md) | icacls, Get-Acl, inheritance |
| 03 | [IIS](windows-03-iis.md) | Install-WindowsFeature, sites, app pools |
| 04 | [Service failures](windows-04-service-failures.md) | Get-Service, sc.exe, recovery, dependencies |
| 05 | [Security updates](windows-05-security-updates.md) | Windows Update, Get-HotFix, Patch Manager |
| 06 | [Scheduled PowerShell tasks](windows-06-scheduled-tasks.md) | Register-ScheduledTask, history |
| 07 | [Event Viewer errors](windows-07-event-viewer.md) | Get-WinEvent, event IDs |
| 08 | [CPU and memory](windows-08-cpu-memory.md) | Get-Counter, Get-Process, perfmon |
| 09 | [Expand storage and partitions](windows-09-expand-storage.md) | Modify Volume, Resize-Partition, Initialize-Disk |
| 10 | [Firewall and connectivity](windows-10-firewall-connectivity.md) | NetSecurity cmdlets, Test-NetConnection |

## AWS

| # | Exercise | Skills |
|---|---|---|
| 01 | [IAM AccessDenied](aws-01-iam-access-denied.md) | instance roles, policy simulator, CloudTrail |
| 02 | [Security groups](aws-02-security-groups.md) | SG references, stateful vs stateless |
| 03 | [SSM access](aws-03-ssm-access.md) | agent, IAM, egress, endpoints |
| 04 | [EBS snapshots](aws-04-ebs-snapshots.md) | snapshot, restore, attach |
| 05 | [CloudFormation failure](aws-05-cloudformation-failure.md) | stack events, rollback states, nested stacks |

## Suggested order

1. **linux-01, windows-01**: get your own admin accounts working first.
2. **linux-02/03, windows-02/03**: then the web servers, so the cross-server tests in the
   networking labs have something to hit.
3. **aws-04** before any disk lab (snapshot habit).
4. Everything else in any order. **aws-05** last, since it deliberately breaks a deployment.

## Rules of the lab

- Faults are set up **by you**, following each guide's scenario section. Nothing in the
  infrastructure injects them.
- Changes you make in the AWS console are drift. Put them back when the exercise ends. Each
  solution has a cleanup section.
- If you break a server beyond repair, rebuilding it is a valid recovery: destroy and deploy
  again, or redeploy with `refresh_amis`.
