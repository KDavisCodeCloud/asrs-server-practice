# Linux 01 — Create users and configure sudo

## Objective

Create local accounts for a small operations team on `asrs-lab-linux`, grant each the
least privilege their role needs through sudo, and prove the access model works
without locking yourself out.

## Scenario

> **Ticket REQ-1187** — Three people are joining the platform team.
> - **alice** (senior admin) needs full root access through sudo.
> - **bob** (app support) may only restart and check the status of the web service
>   (`httpd`) and read its journal. Nothing else.
> - **carol** (contractor) needs a shell account with no elevated rights. Her access
>   ends on the last day of next month and her password must be changed at first login.
>
> All privileged commands must be attributable to a named user in the logs.
> Changes to sudo must not be made by editing `/etc/sudoers` directly.

## Prerequisites

- Stack `asrs-lab` deployed; `asrs-lab-linux` shows **Online** in Fleet Manager.
- Session opened with `aws ssm start-session --target <LinuxInstanceId>` (you land as `ssm-user`).
- Your workstation IAM identity can run `ssm:SendCommand` (needed only for recovery).

## What you need to accomplish

1. Create the three users with home directories and a bash login shell.
2. Give alice full administrative rights using the distribution's existing admin group,
   not a per-user rule.
3. Give bob a sudo rule that allows exactly: restart `httpd`, show `httpd` status,
   and read the `httpd` journal. Any other sudo command must be refused.
4. Make carol's account expire on the required date and force a password change at
   first login. She must not appear in any sudo rule.
5. Put every custom sudo rule in a drop-in file and prove the syntax is valid before
   the change takes effect.
6. Show where sudo usage by alice and bob is recorded.

## Commands or tools to research

- `useradd`, `usermod`, `passwd`, `chage`, `id`, `getent`
- `visudo` (especially its check and file options), `/etc/sudoers`, `/etc/sudoers.d/`
- `sudo -l`, `sudo -u`, `su -`, the `wheel` group
- `Cmnd_Alias` in `man 5 sudoers`, and why command arguments in rules matter
- `journalctl` field filters (`_COMM=`), `/etc/sudoers.d/ssm-agent-users`
- SSM Run Command document `AWS-RunShellScript`

## Validation criteria

- `id alice` shows membership of the admin group; alice can run `sudo whoami` and gets `root`.
- `sudo -l -U bob` lists only the three permitted commands.
- As bob, restarting `httpd` is permitted (it may fail if httpd is not installed yet —
  the point is that sudo allows it); `sudo cat /etc/shadow` is refused.
- `sudo -l -U carol` reports she may not run sudo.
- `chage -l carol` shows the account expiry date and "password must be changed".
- The sudo drop-in file is mode `0440`, owned by `root:root`, and passes a syntax check.
- The journal contains sudo entries naming alice and bob.

## Recovery considerations

- A syntax error in any file under `/etc/sudoers.d/` breaks sudo for **every** user,
  including `ssm-user`. Always validate before installing, never edit live files with
  a plain editor.
- If sudo is broken: Session Manager still logs you in as `ssm-user`, but you cannot
  elevate. Recover from your workstation with Run Command (`AWS-RunShellScript`),
  which executes as root through the SSM agent and does not use sudo.
- Do not remove `ssm-user` from sudo or delete `/etc/sudoers.d/ssm-agent-users`; that is
  how you administer this box.
- Back up any file before editing (`cp -a file file.bak`).
- Last resort: rebuild the instance by destroying and redeploying the stack. Nothing on
  this server is irreplaceable, but practise recovering before you rebuild.

**Interview angle:** "How do you grant least-privilege root access and audit it?" —
groups for roles, command-scoped drop-in rules validated with `visudo -c`, logs per user.

[Solution](solutions/linux-01-users-and-sudo.md) — try it first.
