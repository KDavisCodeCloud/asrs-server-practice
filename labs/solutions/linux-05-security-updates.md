# Solution — Linux 05: OS security updates

Version numbers below are examples. Yours will differ depending on the AMI the stack
resolved at deploy time.

## Background: how AL2023 updates work

- AL2023 repositories are **versioned**. Each release (e.g. `2023.8.20250808`) is an
  immutable snapshot of the repository.
- By default the instance is locked to the release of the installed `system-release`
  package. A plain `dnf upgrade` only installs updates published within that release —
  on a fresh AMI that is usually nothing.
- To get newer packages you explicitly move release: `dnf upgrade --releasever=<version>`
  or `--releasever=latest`. Setting `/etc/dnf/vars/releasever` to `latest` makes it permanent
  (less deterministic; not recommended across a fleet).
- There is no `yum-cron`. Automation is done with SSM Patch Manager or your own timer.

## 1. Record the starting point

```bash
cat /etc/amazon-linux-release
# Amazon Linux release 2023.8.20250808 (Amazon Linux)
rpm -q system-release
# system-release-2023.8.20250808-0.amzn2023.noarch
uname -r
# 6.1.147-172.266.amzn2023.x86_64
rpm -q --last kernel kernel6.12 2>/dev/null | head
```

Save these to the change record:

```bash
{ date -u; cat /etc/amazon-linux-release; uname -r; } | sudo tee /root/CHG-3310-before.txt
```

## 2. Is a newer release available?

```bash
dnf check-release-update
```

Example:

```
WARNING:
  A newer release of "Amazon Linux" is available.

  Available Version: 2023.9.20251014:
    Run the following command to upgrade to 2023.9.20251014:

      dnf upgrade --releasever=2023.9.20251014

    Release notes:
     https://docs.aws.amazon.com/linux/al2023/release-notes/relnotes-2023.9.20251014.html
```

No output means you are already on the newest release. The same message appears in the
login MOTD.

```bash
cat /etc/dnf/vars/releasever 2>/dev/null || echo "not set - locked to installed release"
```

## 3. Review security advisories

Against the current (locked) release — typically empty on a fresh AMI:

```bash
dnf updateinfo summary
```

Against the newer release:

```bash
REL=2023.9.20251014       # from check-release-update
dnf updateinfo summary --releasever=$REL
```

Example:

```
Updates Information Summary: available
    21 Security notice(s)
         2 Critical Security notice(s)
         9 Important Security notice(s)
         8 Medium Security notice(s)
         2 Low Security notice(s)
```

```bash
dnf updateinfo list --security --releasever=$REL
# ALAS2023-2025-1201 Important/Sec. kernel-6.1.155-176.282.amzn2023.x86_64
# ALAS2023-2025-1188 Critical/Sec.  openssl-libs-1:3.2.2-1.amzn2023.0.3.x86_64
# ...

dnf updateinfo list --security --sec-severity=Critical --sec-severity=Important --releasever=$REL \
  | sudo tee /root/CHG-3310-advisories.txt

dnf updateinfo info ALAS2023-2025-1188 --releasever=$REL   # CVEs, description
```

## 4. Rollback point (from your workstation)

```bash
IID=i-0123456789abcdef0
ROOT_VOL=$(aws ec2 describe-instances --instance-ids $IID \
  --query "Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName=='/dev/xvda'].Ebs.VolumeId" \
  --output text)

SNAP=$(aws ec2 create-snapshot --volume-id $ROOT_VOL \
  --description "CHG-3310 pre-patch asrs-lab-linux root" \
  --tag-specifications 'ResourceType=snapshot,Tags=[{Key=Name,Value=asrs-lab-linux-prepatch},{Key=Change,Value=CHG-3310}]' \
  --query SnapshotId --output text)
aws ec2 wait snapshot-completed --snapshot-ids $SNAP
aws ec2 describe-snapshots --snapshot-ids $SNAP --query 'Snapshots[0].[State,Progress]'
# ["completed", "100%"]
```

A crash-consistent snapshot of a running, idle instance is fine for a lab. For production,
quiesce applications or use `create-image` (which reboots by default unless `--no-reboot`).

## 5. Apply Critical and Important security updates

```bash
sudo dnf upgrade --security --sec-severity=Critical --sec-severity=Important \
  --releasever=$REL -y
```

Notes:
- This updates only packages that fix those advisories (plus dependencies), using the newer
  release's repository.
- `system-release` itself is only updated if you run a full `dnf upgrade --releasever=$REL`.
  If you do a security-only update, `/etc/amazon-linux-release` still shows the old
  version — record that honestly. Moving the whole host to the new release
  (`sudo dnf upgrade --releasever=$REL -y`) is the normal AL2023 practice and what most
  teams do monthly.
- Check: `rpm -q system-release` before/after.

## 6. What needs restarting?

`needs-restarting` is a dnf plugin (`dnf-plugins-core`, present on AL2023; install
`dnf-utils` if the command is missing).

```bash
sudo dnf needs-restarting -r; echo "exit=$?"
```

```
Core libraries or services have been updated since boot-up:
  * kernel
  * openssl-libs

Reboot is required to fully utilize these updates.
More information: https://access.redhat.com/solutions/27943
exit=1
```

Exit code `1` = reboot required, `0` = not required. Services only:

```bash
sudo dnf needs-restarting -s
# amazon-ssm-agent.service
# chronyd.service
# systemd-journald.service
```

If only services are listed (no kernel/glibc/systemd), restarting those is enough. Do not
restart `amazon-ssm-agent` casually from your own session; it will disconnect you (it
comes back, but your session will not).

Confirm the new kernel is the GRUB default before rebooting:

```bash
sudo grubby --default-kernel
# /boot/vmlinuz-6.1.155-176.282.amzn2023.x86_64
```

## 7. Reboot and verify

```bash
sudo systemctl reboot
```

Session ends. From the workstation:

```bash
aws ssm describe-instance-information \
  --filters Key=InstanceIds,Values=$IID \
  --query 'InstanceInformationList[0].[PingStatus,PlatformVersion]'
# ["Online", "2023"]
aws ssm start-session --target $IID
```

Then:

```bash
uname -r
# 6.1.155-176.282.amzn2023.x86_64
sudo dnf needs-restarting -r; echo "exit=$?"
# No core libraries or services have been updated since boot-up.
# Reboot should not be necessary.
# exit=0
dnf updateinfo list --security --sec-severity=Critical --sec-severity=Important --releasever=$REL
# (empty)
{ date -u; cat /etc/amazon-linux-release; uname -r; } | sudo tee /root/CHG-3310-after.txt
```

## 8. History and rollback

```bash
sudo dnf history list | head -5
# ID | Command line                                  | Date and time    | Action(s) | Altered
#  3 | upgrade --security --sec-severity=Critical... | 2026-10-23 11:02 | I, U      |   24
sudo dnf history info 3
```

Rollback options, least to most drastic:

1. Boot the previous kernel:
   ```bash
   sudo grubby --info=ALL | grep -E '^(index|kernel)'
   sudo grubby --set-default /boot/vmlinuz-6.1.147-172.266.amzn2023.x86_64
   sudo systemctl reboot
   ```
2. Undo the package transaction (old versions must be reachable; point at the old release):
   ```bash
   sudo dnf history undo 3 --releasever=2023.8.20250808
   ```
   Installed kernels are "installonly" packages; undo removes the new kernel only if it is
   not running.
3. Restore the root volume from the snapshot: EC2 console → Instance → Actions →
   Monitor and troubleshoot → **Replace root volume** → choose the snapshot, or
   `aws ec2 create-replace-root-volume-task --instance-id $IID --snapshot-id $SNAP`.

Kernel live patching (`dnf install kpatch-dnf kpatch-runtime`, `dnf kernel-livepatch -y auto`)
applies some kernel CVE fixes without a reboot. It does not remove the need for periodic reboots.

## Cleanup / reset

```bash
aws ec2 delete-snapshot --snapshot-id $SNAP
```

To repeat the exercise from an unpatched state, destroy and redeploy the stack (the AMI
parameter resolves to the latest AL2023, so it may already be current — the advisory
review steps still apply). Remove `/root/CHG-3310-*` if you want a clean home.

## Common mistakes

- Running `dnf upgrade`, seeing "Nothing to do", and declaring the host patched. The repo
  is release-locked; check `dnf check-release-update`.
- Using `yum-cron`, `amazon-linux-extras` or `yum update --security` habits from Amazon Linux 2.
- Rebooting without checking the GRUB default kernel.
- Skipping the snapshot because "it's just a lab" — the point is practising the change process.
- Restarting `amazon-ssm-agent` from inside the session after `needs-restarting -s`.
- Confusing `uname -r` (running) with `rpm -q kernel` (installed).
