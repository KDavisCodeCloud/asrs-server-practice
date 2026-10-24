# Linux 08 — Disk utilization troubleshooting

## Objective

Find what is consuming disk space and inodes, including space held by deleted-but-open
files and the systemd journal, and reclaim it safely without rebooting.

## Scenario

> **Ticket INC-2117** — Monitoring alerts on `asrs-lab-linux`:
> 1. Root filesystem usage jumped by several GB, but `du` across the filesystem cannot
>    account for it. An application team says they "already deleted the big log".
> 2. The journal is using more space than policy allows (policy: 200 MB maximum).
> 3. The `/scratch` filesystem reports "No space left on device" while `df -h` shows it is
>    mostly empty.
> Reclaim space, explain each case, and put the journal limit in place permanently.

## Prerequisites

- Session on `asrs-lab-linux` with sudo. About 4 GB free on the root filesystem.
- `lsof` may not be installed; installing it is part of the exercise if needed.

**Setup (create the three problems):**

1. Deleted-but-open: create a 3 GB file under `/var/log/asrs-app/` (use `fallocate`). Start
   a background process that holds it open (for example `tail -f` on it, with `nohup`).
   Then delete the file.
2. Journal: write a large volume of messages to the journal (e.g. a loop using `logger`
   with a long payload, tens of thousands of lines). Note `journalctl --disk-usage` before
   and after.
3. Inodes: create a 200 MB file, format it as ext4 with a deliberately tiny inode count
   (around 2,000 inodes), mount it via loop device at `/scratch`, then create small files
   in it until creation fails.

## What you need to accomplish

1. Report filesystem usage by size and by inodes for every mounted filesystem, with types.
2. Find the largest directories on the root filesystem without descending into other mounts.
3. Explain the gap between `df` and `du` on the root filesystem and identify the process
   and file responsible.
4. Reclaim that space without restarting the application process, then also show the
   clean way (restart/signal).
5. Reduce journal usage to under 200 MB now, and configure a permanent 200 MB cap.
6. Explain why `/scratch` is full, prove it, and reclaim it.
7. Leave the root filesystem at roughly its original usage.

## Commands or tools to research

- `df` (`-h`, `-T`, `-i`), `du` (`-x`, `-h`, `--max-depth`, `-s`), `sort -h`, `find -size`, `ncdu` (if available)
- `lsof +L1`, `/proc/<pid>/fd/`, `ls -l` on fd symlinks, `truncate`, `: >` redirection
- `journalctl --disk-usage`, `--vacuum-size`, `--vacuum-time`, `--rotate`,
  `journald.conf` (`SystemMaxUse=`), `/etc/systemd/journald.conf.d/`
- `fallocate`, `mkfs.ext4 -N`, `mount -o loop`, `losetup`, `tune2fs -l`, `stat -f`
- Why XFS (the root filesystem) rarely runs out of inodes and ext4 can

## Validation criteria

- `df -h /` returns to roughly the pre-exercise value while the holding process is still running
  (after task 4's first method).
- `lsof +L1` no longer lists the deleted 3 GB file.
- `journalctl --disk-usage` reports under 200 MB, and a drop-in file sets `SystemMaxUse=200M`;
  `systemctl status systemd-journald` shows no errors after restart.
- `df -i /scratch` showed `IUse%` at 100% while `df -h /scratch` showed low usage; after
  cleanup, inode use has dropped and file creation succeeds.
- `/scratch` is unmounted and its backing file removed at the end.

## Recovery considerations

- Never delete files directly from `/proc/<pid>/fd/`; truncate through the descriptor instead.
  Truncating an fd belonging to a database or a file the app still needs corrupts it — confirm
  what the file is first.
- Do not `rm` active logs to free space; truncate or rotate them.
- Journal vacuuming is irreversible; export what you need first (`journalctl -o export`).
- If the root filesystem reaches 100%, services (including the SSM agent's logging) can fail.
  Keep the test files well below free space. If you lose the session, Run Command
  (`AWS-RunShellScript`) can still delete files; if the agent is also failing, stop/start
  the instance and clean up after boot.
- The loop-mounted `/scratch` is temporary: do not add it to `/etc/fstab`.

**Interview angle:** "`df` says 95% full, `du` says 40% — what's going on?"

[Solution](solutions/linux-08-disk-utilization.md) — try it first.
