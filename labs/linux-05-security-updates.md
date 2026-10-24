# Linux 05 — OS security updates

## Objective

Assess and apply security updates on Amazon Linux 2023 the way a change-managed
operations team would: identify the current release, review advisories by severity,
take a rollback point, patch, determine whether a reboot is required, and verify.

## Scenario

> **Ticket CHG-3310** — Monthly patching for `asrs-lab-linux`. The security team requires:
> - A record of the AL2023 release version before and after patching.
> - A list of outstanding security advisories (ALAS IDs) with severity, before patching.
> - Critical and Important security updates applied; others may wait.
> - A rollback point taken before any package changes.
> - Confirmation of whether the server needs a reboot, and if so, proof it is running the
>   new kernel afterwards.
> Change window: 30 minutes. Nobody is using the server.

## Prerequisites

- Session on `asrs-lab-linux` with sudo; outbound HTTPS to the AL2023 repositories (default).
- AWS CLI on your workstation with permission to create EBS snapshots and to run the
  stack's stop-start workflow (only needed if a reboot misbehaves).
- The root volume ID (find it from the instance's block device mappings).

## What you need to accomplish

1. Record the installed AL2023 release version and the running kernel version.
2. Determine whether a newer AL2023 release is available and what the repositories are
   currently locked to.
3. Produce a list of available security advisories with their severity, and a count by severity.
4. Take a rollback point of the root volume and confirm it completed.
5. Apply only security updates rated Critical or Important, moving to the newer release if
   that is what it takes to receive them.
6. Determine which running services use outdated libraries and whether a full reboot is required.
7. If required, reboot, reconnect, and prove the new kernel is running.
8. Show the package transaction in history and explain how you would roll it back.

## Commands or tools to research

- `/etc/amazon-linux-release`, `/etc/os-release`, `rpm -q system-release`, `uname -r`
- `dnf check-release-update`, `dnf --releasever`, `/etc/dnf/vars/releasever`
- `dnf updateinfo` (`summary`, `list`, `info`, `--sec-severity`), `dnf upgrade --security`
- `dnf needs-restarting` (`-r`, `-s`), `dnf history` (`info`, `undo`, `rollback`)
- `rpm -q --last kernel*`, `grubby` (`--default-kernel`, `--info=ALL`, `--set-default`)
- `aws ec2 create-snapshot`, `aws ec2 wait snapshot-completed`, `aws ec2 describe-instances`
- AL2023 docs: "Deterministic upgrades through versioned repositories"; ALAS bulletin site
- `kpatch` / kernel live patching (awareness only)

## Validation criteria

- Before/after release versions recorded (e.g. from `rpm -q system-release`).
- `dnf updateinfo list --security` (against the release you upgraded to) shows no remaining
  Critical/Important advisories.
- Snapshot is in `completed` state and tagged with the change number.
- `dnf needs-restarting -r` reports no reboot needed (after any reboot).
- `uname -r` matches the newest installed kernel package.
- `dnf history` shows the transaction ID with the packages changed.
- SSM shows the instance `Online` after the reboot.

## Recovery considerations

- Snapshot first. A failed kernel update on a host you can only reach via SSM is the worst
  case: if the instance does not come back Online, you cannot use the serial console
  without prior setup. Restore path: create a volume from the snapshot, stop the instance,
  swap the root volume (or use EC2 "Replace root volume" with the snapshot), start.
- A previous kernel stays installed; you can boot it by setting the GRUB default and rebooting.
- `dnf history undo` cannot downgrade if older versions are not in the repository you are
  pointing at; pin the old `--releasever` when undoing a release upgrade.
- Rebooting ends your Session Manager session; wait 1–3 minutes for the agent to report Online.
- If the instance stays offline after reboot, use the stop-start workflow (stop, then start).
- Snapshots cost money; delete it when the change is closed.

**Interview angle:** "How does patching differ on AL2023 compared with Amazon Linux 2 or RHEL?"
(Versioned repos/deterministic upgrades, no `yum-cron`, `--releasever`.)

[Solution](solutions/linux-05-security-updates.md) — try it first.
