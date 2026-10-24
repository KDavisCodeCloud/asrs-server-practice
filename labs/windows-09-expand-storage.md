# Windows 09 - Expand EBS storage and Windows partitions

## Objective

Bring a new EBS data disk into service in Windows, and grow both the data volume and the system drive online - EBS side and Windows side - safely, with a snapshot first and without a reboot.

## Scenario

> **Ticket CHG-4615** - Standard change
> `asrs-lab-windows` has a 5 GiB data volume attached that nobody ever initialized, and the application team needs it as `D:` labelled `Data`. They also forecast growth: `D:` must go to 10 GiB, and the system drive `C:` must go from 40 GiB to 45 GiB to give Windows Update more headroom. No downtime is allowed. Take snapshots first and record the volume IDs and snapshot IDs in the change.

## Prerequisites

- SSM PowerShell session on Windows; AWS CLI on your workstation with `ec2:DescribeVolumes`, `ec2:CreateSnapshot`, `ec2:ModifyVolume`, `ec2:DescribeVolumesModifications`.
- The optional data volume must be deployed with the stack (it appears in Windows as Disk 1, RAW, offline). If your stack was deployed without it, do only the `C:` part (tasks 1-3 and 7-9).
- Cost note: gp3 is billed per provisioned GiB-month. +10 GiB is a few cents per day, but EBS volumes cannot be shrunk - the extra size stays until the volume is replaced.
- Limitation to know before you start: after a `ModifyVolume`, the same volume cannot be modified again for at least 6 hours. Plan the target size once.

## What you need to accomplish

1. Map Windows disks to EBS volumes: for each Windows disk, identify the EBS volume ID, size, and which one is the root (C:) and which the data volume. Use evidence from inside Windows and from the EC2 API.
2. Snapshot both volumes with descriptive tags and wait until they are complete.
3. Record the current partition layout of Disk 0 (all partitions, not just C:) and confirm C: is the last partition, so it can be extended into new space.
4. Bring the data disk online, make it writable, initialize it as GPT, create one partition using all space with drive letter `D:`, and format NTFS with label `Data`.
5. Write a test file to `D:` and record its hash.
6. Increase the data volume to 10 GiB on the EBS side and extend `D:` to use the new space in Windows.
7. Increase the root volume to 45 GiB on the EBS side; monitor the modification state; extend `C:` once Windows can see the space.
8. Confirm both drives show the new sizes, the test file is intact, and no reboot was needed.
9. Write the rollback plan you would have used if something had gone wrong.

## Commands or tools to research

- `aws ec2 describe-instances` (`BlockDeviceMappings`, `RootDeviceName`), `aws ec2 describe-volumes`, `create-snapshot`, `wait snapshot-completed`, `modify-volume`, `describe-volumes-modifications`
- `Get-Disk` (`SerialNumber`, `AdapterSerialNumber`, `OperationalStatus`, `PartitionStyle`), `Get-PhysicalDisk`, `Get-Partition`, `Get-Volume`
- `Set-Disk` (`-IsOffline`, `-IsReadOnly`), `Initialize-Disk`, `New-Partition`, `Format-Volume`, `Set-Partition`
- `Update-HostStorageCache`, `Update-Disk`, `Get-PartitionSupportedSize`, `Resize-Partition`
- `diskpart` (`rescan`, `list disk`, `select`, `extend`), Disk Management (`diskmgmt.msc`), SAN policy (`Get-StorageSetting`)
- `Get-FileHash`
- EC2 "Replace root volume", create volume from snapshot

## Validation criteria

- `aws ec2 describe-volumes-modifications` shows both modifications `completed` (or `optimizing`, which is already usable).
- `Get-Volume -DriveLetter C, D` shows C: ~45 GB and D: ~10 GB, both `Healthy`, D: labelled `Data`, file system NTFS.
- `Get-Disk` shows Disk 1 `Online`, `GPT`, not read-only.
- The test file hash on `D:` matches the value from task 5.
- `(Get-CimInstance Win32_OperatingSystem).LastBootUpTime` is unchanged from the start of the exercise.
- Two completed snapshots are tagged with CHG-4615.

## Recovery considerations

- Snapshot before every change. Snapshots are crash-consistent; for the data disk, stop writes first if an application were using it.
- Initializing or formatting the wrong disk destroys data. Confirm the disk number by size and serial number (volume ID) before every destructive cmdlet; Disk 0 is the boot disk - never run `Initialize-Disk`/`Clear-Disk` on it.
- `Resize-Partition` (extend) is online and low-risk; shrinking is not part of this change. EBS volumes can never be shrunk - rollback means a new volume from the snapshot.
- If the data disk breaks: detach it, create a new volume from the snapshot in the same AZ, attach it as `xvdf`, bring it online.
- If C: breaks or the instance won't boot: use "Replace root volume" with the pre-change snapshot (instance keeps its ID, ENI and IP), or stop the instance, swap the root volume, start it. SSM access returns once Windows boots.
- If Windows does not see the new size: wait for the modification to reach `optimizing`, then rescan; on very old drivers a reboot may be required, but on Server 2022 with NVMe it is not.

## Interview angle

"How do you grow a Windows system drive on EC2 without downtime?" - Snapshot, `ModifyVolume` (size/type/IOPS online), wait for `optimizing`, rescan disks in Windows, `Resize-Partition` to `Get-PartitionSupportedSize` max (or Disk Management > Extend Volume). Mention the 6-hour cooldown, no shrinking, and the recovery partition that sometimes sits behind C: on custom images.

[Solution](solutions/windows-09-expand-storage.md) — try it first.
