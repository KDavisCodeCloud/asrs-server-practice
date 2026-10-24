# Solution — Linux 01: Create users and configure sudo

All commands run in an SSM session on `asrs-lab-linux` as `ssm-user` unless stated.

## 1. Create the users

```bash
sudo useradd -m -s /bin/bash -c "Alice - Senior Admin" alice
sudo useradd -m -s /bin/bash -c "Bob - App Support"   bob
sudo useradd -m -s /bin/bash -c "Carol - Contractor"  carol

getent passwd alice bob carol
```

Expected:

```
alice:x:1001:1001:Alice - Senior Admin:/home/alice:/bin/bash
bob:x:1002:1002:Bob - App Support:/home/bob:/bin/bash
carol:x:1003:1003:Carol - Contractor:/home/carol:/bin/bash
```

UIDs may differ (the SSM agent created `ssm-user` first, often UID 1001, which shifts
the others).

Set passwords. Session Manager never asks for these, but sudo will ask alice and bob
for their own password:

```bash
sudo passwd alice
sudo passwd bob
sudo passwd carol
```

## 2. Alice — full admin through `wheel`

Amazon Linux 2023 ships this rule in `/etc/sudoers`:

```bash
sudo grep -E '^%wheel' /etc/sudoers
# %wheel  ALL=(ALL)       ALL
```

So adding alice to `wheel` is all that is needed:

```bash
sudo usermod -aG wheel alice
id alice
# uid=1002(alice) gid=1002(alice) groups=1002(alice),10(wheel)
```

`-a` is essential. `usermod -G wheel alice` without `-a` replaces all supplementary groups.

## 3. Bob — command-scoped rule in a drop-in

Write the rule to a temp file, validate it, then install it with the right mode:

```bash
cat <<'EOF' > /tmp/50-app-support
# REQ-1187: bob may restart/inspect httpd only
Cmnd_Alias HTTPD_OPS = /usr/bin/systemctl restart httpd, \
                       /usr/bin/systemctl restart httpd.service, \
                       /usr/bin/systemctl --no-pager status httpd, \
                       /usr/bin/journalctl --no-pager -u httpd, \
                       /usr/bin/journalctl --no-pager -u httpd -n *
bob ALL=(root) HTTPD_OPS
EOF

sudo visudo -cf /tmp/50-app-support
# /tmp/50-app-support: parsed OK

sudo install -o root -g root -m 0440 /tmp/50-app-support /etc/sudoers.d/50-app-support
sudo visudo -c
```

Expected from the full check:

```
/etc/sudoers: parsed OK
/etc/sudoers.d/50-app-support: parsed OK
/etc/sudoers.d/90-cloud-init-users: parsed OK
/etc/sudoers.d/ssm-agent-users: parsed OK
```

(The exact list of files depends on the image.)

Notes:
- Full paths are required; `command -v systemctl` confirms `/usr/bin/systemctl`.
- A command listed **with** arguments only matches those exact arguments. That is what
  stops `sudo systemctl stop sshd`.
- `--no-pager` is deliberate. `systemctl status` and `journalctl` open `less` when run
  interactively; under sudo that pager runs as root and `!sh` inside it gives a root
  shell. This is a classic sudo escape (see GTFOBins) and a good interview point.
  Bob must type the command exactly as listed, e.g. `sudo systemctl --no-pager status httpd`.
- The trailing `-n *` lets bob choose a line count. Wildcards in sudo rules are risky:
  never wildcard arguments for commands that can spawn a shell or write files
  (`less`, `vi`, `tee`, `systemctl edit`, `journalctl` with a pager).

## 4. Carol — expiry and forced password change

```bash
# Last day of next month (GNU date)
EXPIRE=$(date -d "$(date +%Y-%m-01) +2 month -1 day" +%F)
echo "$EXPIRE"

sudo chage -E "$EXPIRE" carol
sudo chage -d 0 carol          # force change at next login
sudo chage -l carol
```

Expected:

```
Last password change                                    : password must be changed
Password expires                                        : password must be changed
Password inactive                                       : password must be changed
Account expires                                         : Nov 30, 2026
...
```

## 5. Validate the access model

```bash
sudo -l -U alice | tail -3
#     (ALL) ALL

sudo -l -U bob
# User bob may run the following commands on ip-10-20-1-x:
#     (root) /usr/bin/systemctl restart httpd, /usr/bin/systemctl restart httpd.service,
#         /usr/bin/systemctl --no-pager status httpd, /usr/bin/journalctl --no-pager -u httpd, ...

sudo -l -U carol
# User carol is not allowed to run sudo on ip-10-20-1-x.
```

Test as each user (from `ssm-user`, `sudo -iu` switches without needing their password):

```bash
sudo -iu alice
sudo whoami            # prompts for alice's password -> root
exit

sudo -iu bob
sudo systemctl restart httpd        # allowed (fails with "Unit httpd.service not found" if not installed - that is fine)
sudo cat /etc/shadow
# Sorry, user bob is not allowed to execute '/usr/bin/cat /etc/shadow' as root on ...
exit
```

Carol's forced change is enforced at password authentication, e.g. `su - carol` from
another account will prompt for a new password.

## 6. Audit trail

AL2023 has no rsyslog by default, so `/var/log/secure` does not exist. sudo logs to the journal:

```bash
sudo journalctl _COMM=sudo --since "1 hour ago" --no-pager
```

Expected lines:

```
sudo[2412]:    alice : TTY=pts/1 ; PWD=/home/alice ; USER=root ; COMMAND=/usr/bin/whoami
sudo[2533]:      bob : command not allowed ; TTY=pts/1 ; PWD=/home/bob ; USER=root ; COMMAND=/usr/bin/cat /etc/shadow
```

Session Manager also records who opened the session (CloudTrail `StartSession`), and
can stream session transcripts to S3/CloudWatch if session logging is configured.

## Recovery: you broke sudo

Simulate (optional): `echo 'bob ALL=(ALL' | sudo tee /etc/sudoers.d/99-broken`. Now:

```
>>> /etc/sudoers.d/99-broken: syntax error near line 1 <<<
sudo: parse error in /etc/sudoers.d/99-broken near line 1
sudo: no valid sudoers sources found, quitting
```

Fix from your workstation with Run Command (runs as root, no sudo involved):

```bash
aws ssm send-command \
  --instance-ids i-0123456789abcdef0 \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["rm -f /etc/sudoers.d/99-broken","visudo -c"]' \
  --query Command.CommandId --output text

aws ssm get-command-invocation --command-id <id> --instance-id i-0123456789abcdef0 \
  --query '[Status,StandardOutputContent]'
```

## Cleanup / reset

```bash
sudo rm -f /etc/sudoers.d/50-app-support
sudo userdel -r alice
sudo userdel -r bob
sudo userdel -r carol
sudo visudo -c
```

## Common mistakes

- `usermod -G` without `-a`, wiping existing groups.
- Editing `/etc/sudoers.d/*` with `vi` directly and saving a syntax error.
- Drop-in file names containing `.` or ending in `~` — sudo silently ignores them
  (e.g. `50-app.support` is skipped).
- Relative command paths (`systemctl` instead of `/usr/bin/systemctl`) — `visudo` rejects them.
- Granting `systemctl *` or `vi /etc/httpd/*` — trivially escalates to root.
- Expecting `/var/log/secure` on AL2023.
- Forgetting `ssm-user` is how you get in; removing its sudo leaves only Run Command.
