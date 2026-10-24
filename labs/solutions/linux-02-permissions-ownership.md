# Solution — Linux 02: Permissions and ownership

## 0. Set up the broken state

```bash
sudo groupadd finance
sudo useradd -m -s /bin/bash -G finance fin1
sudo useradd -m -s /bin/bash -G finance fin2
sudo useradd -m -s /bin/bash audit1
sudo useradd -m -s /bin/bash outsider     # for negative tests

sudo mkdir -p /srv/finance/reports
sudo chmod 0777 /srv/finance/reports

sudo -u fin1 bash -c 'echo "Q3 revenue" > /srv/finance/reports/q3.txt'
ls -l /srv/finance/reports/q3.txt
# -rw-r--r--. 1 fin1 fin1 11 Oct 23 10:00 /srv/finance/reports/q3.txt
sudo -u fin2 rm -f /srv/finance/reports/q3.txt && echo "fin2 deleted fin1's file"
```

Problems visible: file group is `fin1` (private group), mode `644` (fin2 cannot edit),
and the directory lets anyone delete anything.

## 1. Ownership and base mode

```bash
sudo chown root:finance /srv/finance/reports
sudo chmod 3770 /srv/finance/reports
stat -c '%U:%G %a %A' /srv/finance/reports
# root:finance 3770 drwxrws--T
```

`3770` = setgid (2) + sticky (1), then `rwx` owner, `rwx` group, nothing for other.
`T` (capital) means sticky is set but "other" has no execute — expected here.

- **setgid on a directory**: new entries inherit the directory's group (`finance`), and
  new subdirectories inherit the setgid bit.
- **sticky on a directory**: only the file owner, the directory owner, or root can delete
  or rename an entry. Write permission on the file still allows editing its content.

Parent path must be traversable: `namei -l /srv/finance/reports` — `/srv/finance` needs
at least `x` for the users. Default `mkdir` gives `755`, so it is fine.

## 2. Group-writable new files without changing the system umask

The analysts' umask is `0022`, so new files are `644`. A default ACL overrides the umask
for files created in the directory:

```bash
sudo setfacl -d -m g:finance:rwX /srv/finance/reports
```

## 3. Read-only auditor

```bash
# access ACL for the directory and anything already in it
sudo setfacl -R -m u:audit1:rX /srv/finance/reports
# default ACL so future files/dirs inherit it
sudo setfacl -R -d -m u:audit1:rX /srv/finance/reports

getfacl /srv/finance/reports
```

Expected:

```
# file: srv/finance/reports
# owner: root
# group: finance
# flags: -st
user::rwx
user:audit1:r-x
group::rwx
mask::rwx
other::---
default:user::rwx
default:user:audit1:r-x
default:group::rwx
default:group:finance:rwx
default:mask::rwx
default:other::---
```

`X` (capital) = execute only on directories (or files already executable), so audit1
can traverse subdirectories without making files executable. `ls -ld` now shows a `+`
after the mode, indicating an ACL.

If `setfacl` is missing: `sudo dnf install -y acl`.

## 4. Validation

```bash
sudo -u fin1 bash -c 'echo "Q3 revenue" > /srv/finance/reports/q3.txt'
ls -l /srv/finance/reports/q3.txt
# -rw-rw----+ 1 fin1 finance 11 Oct 23 10:05 /srv/finance/reports/q3.txt

getfacl -p /srv/finance/reports/q3.txt | grep -E 'audit1|mask'
# user:audit1:r--
# mask::rw-

# fin2 can edit
sudo -u fin2 bash -c 'echo "reviewed by fin2" >> /srv/finance/reports/q3.txt' && echo edit-ok

# fin2 cannot delete or rename
sudo -u fin2 rm /srv/finance/reports/q3.txt
# rm: cannot remove '/srv/finance/reports/q3.txt': Operation not permitted
sudo -u fin2 mv /srv/finance/reports/q3.txt /srv/finance/reports/x.txt
# mv: cannot move ... Operation not permitted

# auditor reads, cannot write
sudo -u audit1 cat /srv/finance/reports/q3.txt
sudo -u audit1 touch /srv/finance/reports/audit.txt
# touch: cannot touch '/srv/finance/reports/audit.txt': Permission denied

# outsider blocked
sudo -u outsider ls /srv/finance/reports
# ls: cannot open directory '/srv/finance/reports': Permission denied

# subdirectory inherits group, setgid and ACLs
sudo -u fin1 mkdir /srv/finance/reports/2026
stat -c '%G %A' /srv/finance/reports/2026
# finance drwxrws---+
```

Note: setgid group inheritance applies to files created in the directory. A file
**moved** in with `mv` from elsewhere on the same filesystem keeps its original group
and permissions. Copies (`cp`) get the inherited group.

## 5. SUID/SGID and world-writable audit

```bash
sudo find / -xdev -type f \( -perm -4000 -o -perm -2000 \) -exec ls -l {} + 2>/dev/null \
  | sudo tee /root/suid-sgid-audit.txt

sudo find / -xdev \( -type f -o -type d \) -perm -0002 ! -path '/tmp/*' ! -path '/var/tmp/*' \
  ! -path '/dev/shm/*' -exec ls -ld {} + 2>/dev/null | sudo tee /root/world-writable-audit.txt
```

`-xdev` stays on the root filesystem, which skips `/proc`, `/sys`, `/dev` and `/run`.

Typical SUID/SGID results on AL2023 and why they are expected:

| Binary | Why SUID/SGID |
|---|---|
| `/usr/bin/sudo` | Must start as root to elevate |
| `/usr/bin/su` | Switch user |
| `/usr/bin/passwd`, `chage`, `gpasswd`, `newgrp` | Write `/etc/shadow` / `/etc/gshadow` |
| `/usr/bin/mount`, `umount` | User mounts listed in fstab |
| `/usr/bin/crontab` | Only if cronie is installed (writes spool) |
| `/usr/sbin/unix_chkpwd`, `pam_timestamp_check` | PAM password checks |
| `/usr/bin/write` (SGID tty) | Write to other users' terminals |

Anything not owned by a package is suspicious:

```bash
for f in $(awk '{print $NF}' /root/suid-sgid-audit.txt); do rpm -qf "$f" >/dev/null || echo "UNOWNED: $f"; done
rpm -Va 2>/dev/null | grep -E '^.M'      # files whose mode differs from the RPM database
```

## Cleanup / reset

```bash
sudo chattr -i -R /srv/finance 2>/dev/null
sudo rm -rf /srv/finance
for u in fin1 fin2 audit1 outsider; do sudo userdel -r "$u"; done
sudo groupdel finance
sudo rm -f /root/suid-sgid-audit.txt /root/world-writable-audit.txt
```

## Common mistakes

- `chmod 777` "to fix it" — the original problem.
- `chmod g+s` on the files instead of the directory.
- Forgetting the default ACL, so only existing files get audit1 access.
- Using lowercase `x` in ACLs, making every report executable.
- Being surprised by the ACL `mask`: a later `chmod g-w file` lowers the mask, which
  silently limits named ACL entries too.
- Expecting `mv` to change group ownership.
- `find / -perm 4000` (exact match) instead of `-perm -4000` (bit set).
