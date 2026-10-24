# Linux 09 — Expand an EBS volume and the Linux filesystem

## Objective

Grow an EBS volume online and extend the partition and filesystem on top of it without
downtime or data loss, using the correct tool for each filesystem type.

## Scenario

> **Ticket CHG-3342** — The `/data` filesystem on `asrs-lab-linux` is at 90% and growing.
> Approved change: increase the data volume from 5 GiB to 8 GiB online. A snapshot must be
> taken first. Data integrity must be proven before and after. If time allows, also grow the
> root volume from 20 GiB to 25 GiB using the same process.

## Prerequisites

- Stack deployed **with** the optional data volume (stack output `LinuxDataVolumeId` present).
  The volume is attached as `/dev/sdf` but appears in the OS as an NVMe device and is raw.
- Session on `asrs-lab-linux` with sudo.
- AWS CLI on your workstation with `ec2:CreateSnapshot`, `ec2:ModifyVolume`,
  `ec2:DescribeVolumes*` (or use the EC2 console).

**Setup (prepare the data volume yourself):**

1. Identify which NVMe device is the 5 GiB data volume (do not guess — match it to the volume ID).
2. Choose a filesystem: XFS (matches the root filesystem) or ext4. Optionally create a single
   GPT partition first — doing so makes this exercise include the partition-growth step.
3. Format it, mount it at `/data`, and make the mount persistent by UUID in a way that will not
   block boot if the volume is missing.
4. Fill it to roughly 90% with test data, and record checksums of that data.

## What you need to accomplish

1. Snapshot the data volume and wait for completion before changing anything.
2. Increase the volume to 8 GiB using the console or CLI, and track the modification state.
3. Confirm the OS sees the larger block device.
4. If partitioned, grow the partition to fill the device.
5. Grow the filesystem online with the tool appropriate to its type.
6. Prove `/data` is larger, still mounted, and the checksums still match.
7. (Stretch) Repeat for the root volume: 20 GiB → 25 GiB, identifying the correct root partition.
8. Explain why the volume cannot be shrunk back, how long you must wait before modifying it
   again, and what this does to the CloudFormation stack's view of the volume.

## Commands or tools to research

- `lsblk` (`-f`, `-o NAME,SIZE,SERIAL,MOUNTPOINT`), `blkid`, `/dev/disk/by-id/`, `nvme` (optional)
- `parted` / `gdisk`, `mkfs.xfs`, `mkfs.ext4`, `mount`, `/etc/fstab` (`nofail`), `findmnt --verify`
- `sha256sum`, `fallocate` / `dd`
- `aws ec2 create-snapshot`, `aws ec2 wait snapshot-completed`, `aws ec2 modify-volume`,
  `aws ec2 describe-volumes-modifications`
- `growpart` (package `cloud-utils-growpart`), `xfs_growfs`, `resize2fs`, `xfs_info`
- CloudFormation drift detection

## Validation criteria

- Snapshot of the data volume exists in `completed` state, tagged with the change number.
- `describe-volumes-modifications` shows `optimizing` or `completed` with target size 8.
- `lsblk` shows the data device at 8G (and its partition at ~8G if partitioned).
- `df -hT /data` shows roughly 8 GiB total, with the same filesystem type.
- `sha256sum -c` against your recorded checksums reports every file `OK`.
- `/data` remained mounted throughout (no unmount needed).
- Stretch: `df -h /` shows ~25G.
- You can state the 6-hour modification cooldown and the no-shrink rule.

## Recovery considerations

- Snapshot before every resize. Restoring means creating a new volume from the snapshot and
  attaching it (detach the old one first; stop the instance if it is the root volume, or use
  "Replace root volume").
- `growpart` on the wrong device or partition number is the main risk; confirm with `lsblk`
  first. For the root disk, the root filesystem is partition 1; other small partitions are
  boot/EFI and must not be touched.
- If `/etc/fstab` is wrong, boot can stall. Use `nofail` for the data volume and run
  `findmnt --verify` and `mount -a` before any reboot. If an instance does not come back Online
  after a reboot, stop/start it; if still broken, use Run Command or detach the data volume.
- EBS volumes cannot be shrunk. Larger volumes cost more for as long as the stack lives;
  destroying the stack deletes them.
- Out-of-band size changes make the stack drift from its template. Do not "fix" drift by
  editing the template back to a smaller size — the update would fail.

**Interview angle:** "Walk me through growing a full XFS root volume on a live EC2 instance."

[Solution](solutions/linux-09-expand-ebs-filesystem.md) — try it first.
