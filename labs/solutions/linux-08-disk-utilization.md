# Solution — Linux 08: Disk utilization troubleshooting

## 0. Create the problems

Record the baseline first:

```bash
df -h / | tee /tmp/df-before.txt
```

Deleted-but-open file:

```bash
sudo mkdir -p /var/log/asrs-app
sudo fallocate -l 3G /var/log/asrs-app/app.log
sudo nohup tail -f /var/log/asrs-app/app.log >/dev/null 2>&1 &
sudo rm /var/log/asrs-app/app.log
```

Journal flood:

```bash
PAYLOAD=$(head -c 2000 /dev/zero | tr '\0' 'x')
sudo journalctl --disk-usage
for i in $(seq 1 60000); do logger -t asrs-flood "$i $PAYLOAD"; done
sudo journalctl --disk-usage
# Archived and active journals take up 152.0M in the file system.
```

Repeat or increase the count until it exceeds 200 MB. journald rate-limits bursts
(`Suppressed N messages`), so it may take a few minutes; that is expected.

Inode exhaustion:

```bash
sudo fallocate -l 200M /var/tmp/scratch.img
sudo mkfs.ext4 -q -N 2000 /var/tmp/scratch.img
sudo mkdir -p /scratch
sudo mount -o loop /var/tmp/scratch.img /scratch
sudo mkdir /scratch/spool
for i in $(seq 1 5000); do sudo touch /scratch/spool/msg-$i 2>/dev/null || { echo "failed at $i"; break; }; done
# failed at 1990   (approximately)
sudo touch /scratch/one-more
# touch: cannot touch '/scratch/one-more': No space left on device
```

## 1. Usage by size and inodes

```bash
df -hT -x tmpfs -x devtmpfs
# Filesystem     Type  Size  Used Avail Use% Mounted on
# /dev/nvme0n1p1 xfs    20G  5.4G   15G  27% /
# /dev/loop0     ext4  178M   14K  164M   1% /scratch
# ...

df -i -x tmpfs -x devtmpfs
# Filesystem      Inodes IUsed   IFree IUse% Mounted on
# /dev/nvme0n1p1 10485232 60311 10424921    1% /
# /dev/loop0        2000  2000       0  100% /scratch
```

## 2. Largest directories on `/`

```bash
sudo du -xh --max-depth=1 / 2>/dev/null | sort -h | tail -8
# 160M  /var
# 1.2G  /usr
# 2.2G  /
sudo du -xh --max-depth=2 /var 2>/dev/null | sort -h | tail -5
sudo find / -xdev -type f -size +100M -exec ls -lh {} + 2>/dev/null
```

`-x` keeps `du`/`find` on one filesystem, so `/proc`, `/scratch` and others are skipped.

## 3. The `df` / `du` gap

`df` reports ~5.4G used, `du -sx /` ~2.2G. The difference is blocks still allocated to a
file that has no directory entry (link count 0) because a process holds it open.

```bash
sudo dnf install -y lsof     # if missing
sudo lsof +L1
```

```
COMMAND  PID USER   FD   TYPE DEVICE   SIZE/OFF NLINK    NODE NAME
tail    5120 root    3r   REG  259,1 3221225472     0  132001 /var/log/asrs-app/app.log (deleted)
```

`NLINK 0` + `(deleted)` = the culprit. Without `lsof`:

```bash
sudo find /proc/[0-9]*/fd -lname '*(deleted)' -printf '%p -> %l\n' 2>/dev/null
# /proc/5120/fd/3 -> /var/log/asrs-app/app.log (deleted)
```

## 4. Reclaim the space

Without restarting the process — truncate through the descriptor:

```bash
sudo truncate -s 0 /proc/5120/fd/3
df -h /
# back to ~2.4G used
sudo lsof +L1
# entry remains, but SIZE/OFF is now 0
```

The clean way, normally preferred: make the application reopen its log (`systemctl reload`,
`kill -HUP`, or logrotate's `postrotate`) or restart it. Here:

```bash
sudo kill 5120
sudo lsof +L1      # empty
```

Prevention: rotate logs with `copytruncate` or a reopen signal, never `rm` a live log.

## 5. Journal

```bash
sudo journalctl --disk-usage
# Archived and active journals take up 312.0M in the file system.
sudo journalctl --rotate                 # archive active files so they can be vacuumed
sudo journalctl --vacuum-size=150M
# Deleted archived journal /var/log/journal/<machine-id>/system@...journal (64.0M).
# Vacuuming done, freed 160.0M of archived journals from /var/log/journal/<machine-id>.
sudo journalctl --disk-usage
# Archived and active journals take up 148.0M in the file system.
```

Permanent cap via drop-in (do not edit the packaged `journald.conf`):

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=200M\n' | sudo tee /etc/systemd/journald.conf.d/50-size.conf
sudo systemctl restart systemd-journald
systemctl status systemd-journald --no-pager | head -5
sudo journalctl -u systemd-journald -n 3 --no-pager
# System Journal (/var/log/journal/...) is 148.0M, max 200.0M, 51.9M free.
```

AL2023 stores the journal persistently in `/var/log/journal`. The default cap is 10% of the
filesystem (up to 4 GB), i.e. about 2 GB on a 20 GiB root — far above this ticket's policy.

## 6. `/scratch`: out of inodes

Every file needs an inode. ext4 fixes the inode count at `mkfs` time; this one was created
with 2,000, so ~2,000 tiny files fill it although almost no blocks are used.

```bash
sudo tune2fs -l /var/tmp/scratch.img | grep -E 'Inode count|Free inodes'
# Inode count:              2000      (mkfs may round up slightly)
# Free inodes:              0
sudo find /scratch -xdev -type f | cut -d/ -f1-3 | sort | uniq -c | sort -n | tail -3
#    1989 /scratch/spool
```

Reclaim:

```bash
sudo find /scratch/spool -type f -name 'msg-*' -delete
df -i /scratch
# /dev/loop0  2000  12  1988  1% /scratch
sudo touch /scratch/one-more && echo ok
```

Long-term fixes: clean up the producer (mail spools, session files, cache directories), or
recreate the filesystem with more inodes (`mkfs.ext4 -i <bytes-per-inode>`). XFS allocates
inodes dynamically (up to `imaxpct` of space), which is why the root filesystem shows ~1%
inode use and rarely exhausts.

## 7. Confirm

```bash
cat /tmp/df-before.txt; df -h /
```

## Cleanup / reset

```bash
sudo umount /scratch
sudo rm -f /var/tmp/scratch.img
sudo rmdir /scratch
sudo rm -rf /var/log/asrs-app
sudo pkill -f 'tail -f /var/log/asrs-app' 2>/dev/null
# keep the 200M journal cap, or remove it:
sudo rm -f /etc/systemd/journald.conf.d/50-size.conf && sudo systemctl restart systemd-journald
rm -f /tmp/df-before.txt
```

## Common mistakes

- `du /` without `-x`, scanning `/proc` and other mounts and producing noise.
- Restarting the server to "fix" a df/du gap — works, but loses the diagnosis and the uptime.
- `rm /proc/<pid>/fd/N` — removes nothing useful; truncate it instead.
- Vacuuming without `--rotate` and wondering why the active file was not reduced.
- Editing `/etc/systemd/journald.conf` directly instead of using a drop-in.
- Reading only `df -h` and missing inode exhaustion.
- Expecting `/var/log/messages` to be the space hog on AL2023 (the journal is).
