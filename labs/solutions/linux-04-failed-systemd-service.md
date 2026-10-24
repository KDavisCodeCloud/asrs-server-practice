# Solution — Linux 04: Diagnose a failed systemd service

## 0. Build the working service

```bash
sudo useradd --system --no-create-home --shell /sbin/nologin asrsvc
sudo mkdir -p /srv/asrs-status
echo '{"status":"ok"}' | sudo tee /srv/asrs-status/status.json
sudo chmod 755 /srv/asrs-status; sudo chmod 644 /srv/asrs-status/status.json

sudo tee /etc/systemd/system/asrs-status.service >/dev/null <<'EOF'
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
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now asrs-status
curl -s localhost:8080/status.json
# {"status":"ok"}
sudo cp /etc/systemd/system/asrs-status.service /root/asrs-status.service.good
```

Restore between rounds:

```bash
sudo cp /root/asrs-status.service.good /etc/systemd/system/asrs-status.service
sudo systemctl daemon-reload
sudo systemctl reset-failed asrs-status
sudo systemctl restart asrs-status
```

## The standard triage sequence

Use the same five commands every time:

```bash
systemctl status asrs-status --no-pager -l          # state, Result, last log lines
sudo journalctl -u asrs-status -n 30 --no-pager      # full recent log for the unit
systemctl cat asrs-status                            # the config systemd actually sees (+ drop-ins)
systemctl show -p Result,ExecMainStatus,NRestarts,ActiveState,SubState asrs-status
sudo systemd-analyze verify /etc/systemd/system/asrs-status.service
```

## Fault A — misspelled interpreter (`/usr/bin/pyhton3`)

```
     Active: activating (auto-restart) (Result: exit-code) since ...
    Process: 4211 ExecStart=/usr/bin/pyhton3 -m http.server ... (code=exited, status=203/EXEC)
```

Journal:

```
asrs-status.service: Failed to locate executable /usr/bin/pyhton3: No such file or directory
asrs-status.service: Failed at step EXEC spawning /usr/bin/pyhton3: No such file or directory
asrs-status.service: Main process exited, code=exited, status=203/EXEC
```

`203/EXEC`: systemd could not execute the binary (missing, not executable, wrong
interpreter in a script's shebang, or `noexec` mount). Fix the path, `daemon-reload`, restart.

## Fault B — nonexistent user

```
asrs-status.service: Failed to determine user credentials: No such process
asrs-status.service: Failed at step USER spawning /usr/bin/python3: No such process
... status=217/USER
```

`217/USER`: the `User=`/`Group=` lookup failed. Check with `getent passwd <name>`.

## Fault C — missing working directory

```
asrs-status.service: Changing to the requested working directory failed: No such file or directory
asrs-status.service: Failed at step CHDIR spawning /usr/bin/python3: No such file or directory
... status=200/CHDIR
```

Prefixing the value with `-` (`WorkingDirectory=-/path`) makes it optional, but the real
fix is the correct path.

## Fault D — port 80

The process starts, then Python itself fails, so the exit code is the program's own (1),
not a systemd step code:

```
python3[4402]: PermissionError: [Errno 13] Permission denied
asrs-status.service: Main process exited, code=exited, status=1/FAILURE
```

Root cause: a non-root user cannot bind ports below 1024. If you granted the capability
(`AmbientCapabilities=CAP_NET_BIND_SERVICE`), the next error would be the port conflict
with httpd:

```
OSError: [Errno 98] Address already in use
```

confirmed by `sudo ss -ltnp 'sport = :80'` showing httpd. Fix: back to 8080.

## Fault E — edited but not reloaded

```bash
sudo systemctl restart asrs-status
# Warning: The unit file, source configuration file or drop-ins of asrs-status.service
# changed on disk. Run 'systemctl daemon-reload' to reload units.
systemctl show -p NeedDaemonReload asrs-status
# NeedDaemonReload=yes
```

The service runs with the **old** in-memory definition, so your change (e.g. a new port)
silently does not apply. `systemctl cat` shows the file on disk, which is misleading here.
Fix: `sudo systemctl daemon-reload && sudo systemctl restart asrs-status`.

## Fault F — no `ExecStart=`

```
     Loaded: bad-setting (Reason: Unit asrs-status.service has a bad unit file setting.)
asrs-status.service: Service has no ExecStart=, ExecStop=, or SuccessAction=. Refusing.
```

`systemctl restart` returns `Failed to restart asrs-status.service: Unit asrs-status.service has a bad unit file setting.`
The unit never runs, so `Restart=` never applies.

## Fault G — invalid value (`Restart=sometimes`)

```
/etc/systemd/system/asrs-status.service:12: Failed to parse service restart specifier, ignoring: sometimes
```

The service **starts fine**: systemd ignores most invalid values with only a warning and
uses the default (`Restart=no`). The trap is that a later crash will not restart.
`systemd-analyze verify` surfaces it; `systemctl show -p Restart asrs-status` returns `no`.

## Why `Restart=on-failure` did not save you

| Fault | Behaviour |
|---|---|
| A, B, C, D | Restarts every 5 s forever (`activating (auto-restart)`, `NRestarts` climbing). The default start limit is 5 starts in 10 s; with `RestartSec=5` it is never reached, so it loops. Manual restarts on top can trip it: `start-limit-hit`. |
| E | No failure; wrong config silently in use. |
| F | Unit never loads; restart logic never runs. |
| G | Restart policy itself was discarded. |

Restart policies handle transient crashes, not deterministic configuration errors.

If you see `Result: start-limit-hit`:

```bash
sudo systemctl reset-failed asrs-status
sudo systemctl start asrs-status
```

## Fix confirmation

```bash
systemctl is-active asrs-status; systemctl is-enabled asrs-status
# active
# enabled
systemctl --failed --no-legend
# (empty)
curl -s localhost:8080/status.json
# {"status":"ok"}
```

## Drop-in override

```bash
sudo systemctl edit asrs-status
```

In the editor, between the comment markers, add:

```ini
[Service]
Environment=ASRS_ENV=lab
```

Non-interactive equivalent:

```bash
sudo mkdir -p /etc/systemd/system/asrs-status.service.d
printf '[Service]\nEnvironment=ASRS_ENV=lab\n' | sudo tee /etc/systemd/system/asrs-status.service.d/override.conf
sudo systemctl daemon-reload
```

Then:

```bash
sudo systemctl restart asrs-status
systemctl cat asrs-status | tail -3
# # /etc/systemd/system/asrs-status.service.d/override.conf
# [Service]
# Environment=ASRS_ENV=lab
systemctl show -p Environment asrs-status
# Environment=ASRS_ENV=lab
sudo cat /proc/$(systemctl show -p MainPID --value asrs-status)/environ | tr '\0' '\n' | grep ASRS
# ASRS_ENV=lab
```

`systemctl edit` runs `daemon-reload` for you; a manual drop-in does not.

## Cleanup / reset

```bash
sudo systemctl disable --now asrs-status
sudo rm -rf /etc/systemd/system/asrs-status.service /etc/systemd/system/asrs-status.service.d
sudo systemctl daemon-reload
sudo systemctl reset-failed
sudo rm -rf /srv/asrs-status /root/asrs-status.service.good
sudo userdel asrsvc
```

## Common mistakes

- Reading only `systemctl status` (last 10 lines) and missing the first error; use the journal.
- Forgetting `daemon-reload`, then "fixing" the file repeatedly with no effect.
- Treating `status=1/FAILURE` as a systemd problem — it is the application's exit code; read
  the application's own output in the journal.
- Editing a vendor unit in `/usr/lib/systemd/system/`; package updates overwrite it. Use drop-ins.
- Assuming a parsed-with-warnings unit is correct because it started.
- Running `journalctl` without sudo as `ssm-user` and seeing only partial logs.
