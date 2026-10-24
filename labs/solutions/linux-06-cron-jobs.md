# Solution — Linux 06: Create and troubleshoot cron jobs

## 0. Set up the broken state

```bash
sudo tee /usr/local/bin/disk-report.sh >/dev/null <<'EOF'
#!/bin/bash
echo "$(date +%F %T) $(df -h / | tail -1)" >> /var/log/asrs/disk-report.log
EOF

printf '%s\n%s' \
  '*/15 * * * * /usr/local/bin/disk-report.sh' \
  "30 2 * * * root find /var/log/asrs -name '*.log' -mtime +7 -delete" \
  | sudo tee /etc/cron.d/asrs-reports >/dev/null
sudo chmod 664 /etc/cron.d/asrs-reports
```

## 1. Install cron

```bash
systemctl status crond
# Unit crond.service could not be found.

sudo dnf install -y cronie
sudo systemctl enable --now crond
systemctl is-enabled crond; systemctl is-active crond
# enabled
# active
```

`cronie` also pulls in `cronie-anacron`, which runs `/etc/cron.daily`, `weekly`, `monthly`
via `/etc/anacrontab` (catching up after downtime).

## 2. Find every fault

Look at what crond says first:

```bash
sudo journalctl -u crond --no-pager -n 20
```

Expected findings:

```
crond[2210]: (CRON) STARTUP (1.5.7)
crond[2210]: (root) BAD FILE MODE (/etc/cron.d/asrs-reports)
```

Work through them:

| # | Fault | Evidence | Fix |
|---|---|---|---|
| 1 | No cron daemon | `crond.service could not be found` | Install `cronie` |
| 2 | `/etc/cron.d` file group-writable | `BAD FILE MODE` | `chmod 644`, owner root |
| 3 | No trailing newline | Last line silently ignored by cron | Add newline |
| 4 | Missing user field on line 1 | In `/etc/cron.d` the 6th field is the user; cron would try to run as user `/usr/local/bin/disk-report.sh` → `ERROR (getpwnam() failed)` | Add `root` |
| 5 | Script not executable | `/bin/sh: /usr/local/bin/disk-report.sh: Permission denied` (only visible if output is captured) | `chmod 755` |
| 6 | Unquoted `date` format | `date +%F %T` passes `%T` as a second argument → `date: extra operand '%T'` | Quote it: `date '+%F %T'` |
| 7 | Log directory missing | `No such file or directory` | Create `/var/log/asrs` |

Related classic: if you put `date +%F` directly in a crontab line (not in a script), cron
turns every unescaped `%` into a newline. Escape it as `\%`, or keep logic in scripts.

Fix:

```bash
sudo tee /usr/local/bin/disk-report.sh >/dev/null <<'EOF'
#!/bin/bash
set -euo pipefail
echo "$(date '+%F %T') $(df -h / | tail -1)" >> /var/log/asrs/disk-report.log
EOF
sudo chmod 755 /usr/local/bin/disk-report.sh
sudo install -d -m 755 /var/log/asrs

sudo /usr/local/bin/disk-report.sh && tail -1 /var/log/asrs/disk-report.log
# 2026-10-23 11:14:02 /dev/nvme0n1p1   20G  2.1G   18G  11% /
```

Test the cleanup with `-print` before trusting `-delete`:

```bash
sudo find /var/log/asrs -name '*.log' -mtime +7 -print
```

Rewrite the cron file (step 3 adds logging):

```bash
sudo tee /etc/cron.d/asrs-reports >/dev/null <<'EOF'
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
MAILTO=""
*/15 * * * * root /usr/local/bin/disk-report.sh 2>&1 | /usr/bin/logger -t disk-report
30 2 * * *   root /usr/bin/find /var/log/asrs -name '*.log' -mtime +7 -delete 2>&1 | /usr/bin/logger -t asrs-cleanup
EOF
sudo chown root:root /etc/cron.d/asrs-reports
sudo chmod 644 /etc/cron.d/asrs-reports
```

crond rescans `/etc/cron.d` every minute; no restart needed. `cat -A` would show `$` at
the end of every line, confirming newlines.

Note the cleanup pattern `*.log` would delete `disk-report.log` itself once it is 7 days
since last modification — it never will be, because it is appended every 15 minutes. If you
rotate reports into dated files, the job cleans those up. Point this out in the incident note.

## 3. Output with no mail system

There is no MTA on AL2023, so cron's default "mail the output" goes nowhere
(`(CRON) info (No MTA installed, discarding output)` in the journal). Options used above:
pipe to `logger` (journal), or redirect to a file (`>> /var/log/asrs/cron.err 2>&1`).
`MAILTO=""` suppresses mail attempts.

## 4. User crontab for ssm-user

```bash
crontab -e      # as ssm-user
```

Add:

```
* * * * * /usr/bin/date >> $HOME/cron-heartbeat.txt 2>&1
```

Non-interactive alternative:

```bash
( crontab -l 2>/dev/null; echo '* * * * * /usr/bin/date >> $HOME/cron-heartbeat.txt 2>&1' ) | crontab -
crontab -l
sudo ls -l /var/spool/cron/ssm-user
sleep 125; cat ~/cron-heartbeat.txt
```

User crontabs have **no** user field (5 time fields + command). `$HOME` is set by cron for
user crontabs. Remove when proven: `crontab -r`.

If `/etc/cron.allow` exists, only users listed there can use `crontab`; otherwise
`/etc/cron.deny` (empty by default) blocks listed users.

## 5. Prove execution from logs

```bash
sudo journalctl -u crond --since "-30 min" --no-pager | grep CMD
# crond[3021]: (root) CMD (/usr/local/bin/disk-report.sh 2>&1 | /usr/bin/logger -t disk-report)
# crond[3087]: (ssm-user) CMD (/usr/bin/date >> $HOME/cron-heartbeat.txt 2>&1)

sudo journalctl -t disk-report --since "-30 min" --no-pager     # any script errors
tail -3 /var/log/asrs/disk-report.log
```

On AL2023 there is no `/var/log/cron` (no rsyslog). The journal is the cron log.

Check timing: `date` and `timedatectl` show UTC; `*/15` fires at :00/:15/:30/:45 UTC.

## 6. Same job as a systemd timer

```bash
sudo tee /etc/systemd/system/disk-report.service >/dev/null <<'EOF'
[Unit]
Description=ASRS disk usage report

[Service]
Type=oneshot
ExecStart=/usr/local/bin/disk-report.sh
EOF

sudo tee /etc/systemd/system/disk-report.timer >/dev/null <<'EOF'
[Unit]
Description=Run ASRS disk usage report every 15 minutes

[Timer]
OnCalendar=*:0/15
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
EOF

systemd-analyze calendar '*:0/15'
#   Original form: *:0/15
# Normalized form: *-*-* *:00/15:00
#     Next elapse: Fri 2026-10-23 11:30:00 UTC

sudo systemctl daemon-reload
sudo systemctl enable --now disk-report.timer
```

Disable the cron version of that job (keep the cleanup line if you want it on cron):

```bash
sudo sed -i 's|^\*/15|#*/15|' /etc/cron.d/asrs-reports
```

Validate:

```bash
systemctl list-timers disk-report.timer --no-pager
# NEXT                        LEFT     LAST  PASSED  UNIT              ACTIVATES
# Fri 2026-10-23 11:30:00 UTC 9min left -    -       disk-report.timer disk-report.service

sudo systemctl start disk-report.service       # run once now
sudo journalctl -u disk-report.service -n 5 --no-pager
# systemd[1]: Starting disk-report.service - ASRS disk usage report...
# systemd[1]: disk-report.service: Deactivated successfully.
# systemd[1]: Finished disk-report.service - ASRS disk usage report.
```

Comparison for the ticket:

| | cron | systemd timer |
|---|---|---|
| Installed on AL2023 | No (`cronie` needed) | Yes |
| Logging | Only what you redirect | stdout/stderr and exit code in the journal automatically |
| Missed runs while powered off | Lost (except anacron jobs) | `Persistent=true` runs once at boot |
| Status / last result | None | `systemctl status`, `list-timers` |
| Overlap protection | None | A oneshot unit will not start twice concurrently |
| Resource limits, sandboxing | No | Full unit options (`MemoryMax=`, `ProtectSystem=`) |

Recommendation: timers on AL2023, cron only where tooling expects a crontab.

## Cleanup / reset

```bash
crontab -r 2>/dev/null; rm -f ~/cron-heartbeat.txt
sudo systemctl disable --now disk-report.timer
sudo rm -f /etc/systemd/system/disk-report.{service,timer}
sudo systemctl daemon-reload
sudo rm -f /etc/cron.d/asrs-reports /usr/local/bin/disk-report.sh
sudo rm -rf /var/log/asrs
sudo systemctl disable --now crond
sudo dnf remove -y cronie cronie-anacron
```

## Common mistakes

- Assuming cron exists on AL2023.
- Mixing up formats: user field present in `/etc/crontab` and `/etc/cron.d/*`, absent in `crontab -e`.
- File in `/etc/cron.d` writable by group/other, or not owned by root. Keep names simple:
  cronie skips backup-style names (`~`, `.rpmsave`, `.rpmnew`), and Debian's cron also
  skips any name containing `.` — a portability trap.
- No trailing newline on the last line.
- Unescaped `%` in a crontab command line.
- Relying on interactive `PATH`; cron's default is `/usr/bin:/bin`.
- Looking for `/var/log/cron` or waiting for cron mail.
- Testing a `-delete` cleanup without `-print` first.
