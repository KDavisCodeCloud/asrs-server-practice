# Linux 02 — Permissions and ownership

## Objective

Build a shared team directory with correct ownership, group inheritance and
deletion protection, grant a one-off exception with ACLs, and audit the filesystem
for risky permission bits.

## Scenario

> **Ticket INC-2056** — Finance analysts keep overwriting and deleting each other's
> reports in `/srv/finance/reports`. Files created by one analyst are unreadable by the
> others because they end up owned by the creator's private group. An auditor (`audit1`)
> must be able to read, but never change, everything in that directory. Separately, the
> security team wants a list of any SUID/SGID binaries and world-writable files outside
> `/tmp` and `/proc`. Fix the directory and produce the audit list.

## Prerequisites

- Session on `asrs-lab-linux` as `ssm-user` (sudo available).
- Comfortable creating users (Linux 01). This exercise creates its own test users.

**Setup (reproduce the broken state):**

1. Create users `fin1`, `fin2` and `audit1`, and a group `finance` containing `fin1` and `fin2`.
2. Create `/srv/finance/reports` owned by `root:root` with mode `0777`.
3. As `fin1`, create a report file in it; as `fin2`, delete that file. Note that it works —
   that is the bug.

## What you need to accomplish

1. `/srv/finance/reports` is owned by root and group `finance`; only `finance` members
   (and root) can create files there; nobody else can list it — except `audit1`.
2. New files and subdirectories automatically belong to group `finance`, regardless of
   who creates them.
3. New files are group-readable and group-writable by default for analysts, without
   changing the system-wide umask.
4. An analyst can edit, but cannot delete or rename, a file another analyst owns.
5. `audit1` can read every existing and future file in the tree, and cannot write.
6. Produce a list of SUID/SGID files and world-writable files on the root filesystem
   (excluding pseudo-filesystems), and explain why each SUID binary you find is expected.

## Commands or tools to research

- `chown`, `chgrp`, `chmod` (symbolic and octal, special bits `u+s`, `g+s`, `+t`)
- `umask`, `stat`, `ls -ld`, `namei -l`
- `setfacl`, `getfacl`, default ACLs vs access ACLs, the ACL mask
- `find` with `-perm`, `-xdev`, `-type`
- `sudo -u <user>` for testing as another user
- `man 7 inode` (the meaning of the sticky bit on directories), `chattr` / `lsattr`

## Validation criteria

- `stat -c '%U:%G %a' /srv/finance/reports` shows root, group finance, and a mode that
  includes setgid and sticky with no "other" access.
- A file created by `fin1` is group `finance`, readable and writable by `fin2`.
- `fin2` deleting `fin1`'s file fails with "Operation not permitted".
- `audit1` can `cat` a file created after your change and cannot `touch` a new file.
- A user not in `finance` and not `audit1` gets "Permission denied" listing the directory.
- `getfacl` shows a default ACL entry for `audit1`.
- Your audit list exists and the SUID entries are explained.

## Recovery considerations

- Never run recursive `chmod`/`chown` on `/`, `/usr`, `/etc` or a path built from a variable
  that could be empty. A recursive mistake on system paths can break sudo
  (`/usr/bin/sudo` must stay SUID root) and sshd/SSM; recovery is Run Command
  (`AWS-RunShellScript`, runs as root) or `rpm --setperms` / `rpm --setugids` per package.
- Record current state before changes: `getfacl -R /srv/finance > /root/finance.acl`
  can be restored with `setfacl --restore`.
- The root volume is small and cheap to snapshot; take one before experimenting outside `/srv`.
- `chattr +i` makes files immutable even to root — remember to remove it during cleanup.

**Interview angle:** "Explain setgid on directories and the sticky bit, and when you'd
reach for ACLs instead of groups."

[Solution](solutions/linux-02-permissions-ownership.md) — try it first.
