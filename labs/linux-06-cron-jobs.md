# Linux 06 — Create and troubleshoot cron jobs

## Objective

Install a cron daemon on Amazon Linux 2023, schedule user and system jobs, diagnose
jobs that silently fail, and implement the same job as a systemd timer for comparison.

## Scenario

> **Ticket INC-2088** — A disk-usage report script was copied from an old Amazon Linux 2
> server to `asrs-lab-linux`. It is supposed to run every 15 minutes and append to
> `/var/log/asrs/disk-report.log`, plus a nightly cleanup at 02:30 UTC that deletes
> reports older than 7 days. Nothing has ever been written. The engineer who set it up
> says "cron must be broken". Make both jobs work, prove they run, and propose whether
> this server should use cron or systemd timers going forward.

## Prerequisites

- Session on `asrs-lab-linux` with sudo.
- Note: AL2023 does **not** ship a cron daemon by default. That is part of the fault.

**Setup (reproduce the broken state):**

1. Create `/usr/local/bin/disk-report.sh` containing:

```bash
#!/bin/bash
echo "$(date +%F %T) $(df -h / | tail -1)" >> /var/log/asrs/disk-report.log
```

   Leave it **not executable**.
2. Create `/etc/cron.d/asrs-reports` with the following two lines and **no trailing newline**
   (use `printf`, not `echo`), and make the file group-writable:

```
*/15 * * * * /usr/local/bin/disk-report.sh
30 2 * * * root find /var/log/asrs -name '*.log' -mtime +7 -delete
```

3. Do not create `/var/log/asrs`.

## What you need to accomplish

1. Install and enable a cron daemon so it starts at boot.
2. Find every reason the 15-minute job cannot run (there are several) and fix them
   without changing the schedule.
3. Make the job's output and errors go somewhere you can read, since there is no local
   mail system.
4. Add a user-level crontab for `ssm-user` that writes the date to a file in its home
   directory every minute, then remove it once proven.
5. Prove from logs (not just the output file) that cron executed each job.
6. Implement the 15-minute report as a systemd service + timer instead, disable the cron
   version, and compare: logging, missed runs after downtime, and visibility.

## Commands or tools to research

- `dnf` package `cronie` (and `cronie-anacron`), `crond.service`
- `crontab` (`-e`, `-l`, `-r`, `-u`), `/etc/cron.d/`, `/etc/crontab`, `/var/spool/cron/`,
  `/etc/cron.allow`, `/etc/cron.deny`
- `man 5 crontab` — fields, the user field in system crontabs, `%` handling, `PATH`, `MAILTO`
- `journalctl -u crond`, `timedatectl` (cron uses system time zone)
- `systemd.timer` (`OnCalendar=`, `Persistent=`, `RandomizedDelaySec=`),
  `systemctl list-timers`, `systemd-analyze calendar`
- `logger`, `systemd-cat`

## Validation criteria

- `systemctl is-enabled crond` and `is-active crond` both succeed.
- `/var/log/asrs/disk-report.log` gains a line every 15 minutes (at :00, :15, :30, :45).
- `journalctl -u crond` shows `CMD (...)` lines for the job at the expected times.
- `crontab -l` as `ssm-user` shows the per-minute job; its output file updates each minute.
- After the switch, `systemctl list-timers` shows the timer with a next-run time, and
  `journalctl -u <your service>` shows each run.
- `/etc/cron.d/asrs-reports` no longer runs the 15-minute job (commented or removed).

## Recovery considerations

- A cleanup job with `-delete` is destructive. Test the `find` expression with `-print`
  first, and never point it at a path that could be empty or `/`.
- Cron failures are silent by design; always capture output while developing.
- Removing `cronie` later does not remove `/var/spool/cron` crontabs; clean up explicitly.
- Time zone is UTC by default on EC2; do not change the system time zone to "fix" schedules
  on a shared server — use `CRON_TZ` or a timer's `OnCalendar` time zone instead.
- Nothing here needs a snapshot.

**Interview angle:** "A cron job works when you run it by hand but not from cron — why?"
(Environment/PATH, permissions, `%`, missing user field, output going nowhere.)

[Solution](solutions/linux-06-cron-jobs.md) — try it first.
