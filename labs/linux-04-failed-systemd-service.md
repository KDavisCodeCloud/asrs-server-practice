# Linux 04 — Diagnose a failed systemd service

## Objective

Use systemd and the journal to find the root cause of a service that will not start,
fix it, and make the fix survive reboots — for several different classes of failure.

## Scenario

> **Ticket INC-2073** — Monitoring reports `asrs-status.service` on `asrs-lab-linux` is
> in a failed state after last night's change window. The service serves a small JSON
> status feed on TCP 8080. The change log is incomplete. Restore service and state
> the root cause for each failure in the incident notes.

## Prerequisites

- Session on `asrs-lab-linux` with sudo.
- Python 3 is present on AL2023 by default (`python3 --version`).

**Setup (build the working service first):**

1. Create a system user `asrsvc` with no login shell and no home directory.
2. Create `/srv/asrs-status/status.json` containing `{"status":"ok"}`, readable by `asrsvc`.
3. Create `/etc/systemd/system/asrs-status.service` with:

```ini
[Unit]
Description=ASRS status feed
After=network-online.target
Wants=network-online.target

[Service]
User=asrsvc
WorkingDirectory=/srv/asrs-status
ExecStart=/usr/bin/python3 -m http.server 8080 --bind 0.0.0.0
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

4. Reload systemd, enable and start the unit, and confirm `curl localhost:8080/status.json` works.

**Introduce faults — one at a time.** Restore the working unit between rounds. Ideally
have someone else pick one without telling you.

- A. Misspell the interpreter path in `ExecStart`.
- B. Change `User=` to a user that does not exist.
- C. Change `WorkingDirectory=` to a path that does not exist.
- D. Change the port to `80` while httpd (Linux 03) is running.
- E. Edit the unit file (any visible change, e.g. the port) but do not reload systemd, then restart.
- F. Delete the `ExecStart=` line.
- G. Set an invalid value in the `[Service]` section (e.g. `Restart=sometimes`) — note
  carefully what systemd does with it.

After each fault, run `systemctl restart asrs-status` and then diagnose without looking at
what you changed.

## What you need to accomplish

1. For each fault, identify the root cause using only systemd and journal output.
2. Record the result/exit status systemd reports and what it means.
3. Fix the unit and bring the service back to `active (running)`.
4. Confirm the service is enabled and will start at boot.
5. Explain how `Restart=on-failure` behaved for each fault (did it loop, give up, or never
   apply?) and why it could not fix the problem.
6. Make a local override (not editing the vendor file) that sets an environment variable
   for the service, and show it took effect.

## Commands or tools to research

- `systemctl status`, `systemctl cat`, `systemctl show -p`, `systemctl is-failed`,
  `systemctl reset-failed`, `systemctl daemon-reload`, `systemctl edit`
- `journalctl -u`, `-b`, `-xe`, `-p err`, `--since`
- `systemd-analyze verify`, `systemd-analyze blame`
- `man systemd.exec` (section "Process Exit Codes"), `man systemd.service`
  (`StartLimitBurst`, `StartLimitIntervalSec`)
- `ss -ltnp`, `getent passwd`, `namei`

## Validation criteria

- `systemctl is-active asrs-status` returns `active`; `is-enabled` returns `enabled`.
- `curl -s localhost:8080/status.json` returns `{"status":"ok"}`.
- `systemctl --failed` lists no units.
- For each fault you can quote the journal line that identified the cause.
- `systemctl cat asrs-status` shows a drop-in override file, and
  `systemctl show -p Environment asrs-status` shows your variable.

## Recovery considerations

- Keep a known-good copy of the unit (`cp` to `/root/asrs-status.service.good`) before
  each fault round.
- Repeated failures can hit the start rate limit; the unit then refuses to start until
  you clear the failed state.
- Do not practise faults on `amazon-ssm-agent` — stopping it ends your session, and Run
  Command cannot help because it also depends on the agent. Recovery would be a stop/start
  of the instance (stop-start workflow); the agent is enabled at boot.
- Faults here are confined to one custom unit; no snapshot is required.

**Interview angle:** "A service shows `failed` — walk me through your first five commands."

[Solution](solutions/linux-04-failed-systemd-service.md) — try it first.
