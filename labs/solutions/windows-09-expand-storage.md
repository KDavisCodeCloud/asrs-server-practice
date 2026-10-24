# Solution: Windows 09 - Expand EBS storage and Windows partitions

Record the boot time first so you can prove no reboot happened:

```powershell
(Get-CimInstance Win32_OperatingSystem).LastBootUpTime
```

## 1. Map Windows disks to EBS volumes

Workstation:

```bash
aws ec2 describe-instances --instance-ids "$WIN_ID" \
  --query 'Reservations[0].Instances[0].{Root:RootDeviceName,Disks:BlockDeviceMappings[].[DeviceName,Ebs.VolumeId]}'
ROOT_VOL=$(aws ec2 describe-instances --instance-ids "$WIN_ID" \
  --query "Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName=='/dev/sda1'].Ebs.VolumeId" --output text)
DATA_VOL=$(aws ec2 describe-instances --instance-ids "$WIN_ID" \
  --query "Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName=='xvdf' || DeviceName=='/dev/xvdf'].Ebs.VolumeId" --output text)
aws ec2 describe-volumes --volume-ids "$ROOT_VOL" "$DATA_VOL" \
  --query 'Volumes[].[VolumeId,Size,VolumeType,Encrypted,AvailabilityZone,Attachments[0].Device]' --output table
```

```
{ "Root": "/dev/sda1", "Disks": [["/dev/sda1","vol-0aaa..."],["xvdf","vol-0bbb..."]] }
|  vol-0aaa...  |  40 |  gp3 |  True |  us-east-1a |  /dev/sda1  |
|  vol-0bbb...  |   5 |  gp3 |  True |  us-east-1a |  xvdf       |
```

Windows (t3 is a Nitro instance, so EBS volumes are NVMe disks; the device names `sda1`/`xvdf` mean nothing inside Windows - use the serial number):

```powershell
Get-Disk | Select-Object Number, FriendlyName,
    @{n='VolumeId';e={ $_.SerialNumber -replace '_[^ ]*$' -replace '^vol', 'vol-' }},
    @{n='SizeGiB';e={ $_.Size / 1GB }}, PartitionStyle, OperationalStatus, IsReadOnly, IsBoot |
    Format-Table -AutoSize
```

```
Number FriendlyName               VolumeId        SizeGiB PartitionStyle OperationalStatus IsReadOnly IsBoot
------ ------------               --------        ------- -------------- ----------------- ---------- ------
     0 Amazon Elastic Block Store vol-0aaa...          40 MBR            Online                 False   True
     1 Amazon Elastic Block Store vol-0bbb...           5 RAW            Offline                 True  False
```

The NVMe serial number is the volume ID without the hyphen plus a suffix (`vol0bbb..._00000001.`). Match it against the API output. Disk 1 is offline and read-only because of the Windows SAN policy for newly discovered disks (`Get-StorageSetting | Select-Object NewDiskPolicy`).

## 2. Snapshots

Workstation:

```bash
for V in "$ROOT_VOL" "$DATA_VOL"; do
  aws ec2 create-snapshot --volume-id "$V" --description "CHG-4615 pre-resize $V" \
    --tag-specifications "ResourceType=snapshot,Tags=[{Key=Name,Value=CHG-4615-$V},{Key=Change,Value=CHG-4615}]" \
    --query SnapshotId --output text
done
aws ec2 describe-snapshots --owner-ids self --filters Name=tag:Change,Values=CHG-4615 \
  --query 'Snapshots[].[SnapshotId,VolumeId,State,Progress]' --output table
aws ec2 wait snapshot-completed --filters Name=tag:Change,Values=CHG-4615 --owner-ids self
```

## 3. Partition layout of Disk 0

```powershell
Get-Partition -DiskNumber 0 | Select-Object PartitionNumber, DriveLetter, Type,
    @{n='OffsetGB';e={ [math]::Round($_.Offset / 1GB, 2) }}, @{n='SizeGB';e={ [math]::Round($_.Size / 1GB, 2) }}
```

Example (MBR-booted AMI):

```
PartitionNumber DriveLetter Type  OffsetGB SizeGB
--------------- ----------- ----  -------- ------
              1           C IFS       0.00  40.00
```

On a UEFI/GPT image you will also see `System` (EFI) and `Reserved` (MSR) partitions before C:. What matters is that C: is the **last** partition. If a `Recovery` partition sits after C:, Windows cannot extend C: into the new space until that partition is moved or deleted (a common problem on custom images, not usually on AWS AMIs).

## 4. Bring the data disk into service

Double-check you have the right disk (5 GiB, `RAW`, `IsBoot False`) before each destructive step:

```powershell
$d = Get-Disk -Number 1
if ($d.PartitionStyle -ne 'RAW' -or $d.IsBoot -or $d.Size -ne 5GB) { throw "Disk 1 is not the empty 5 GiB data disk - stop." }

Set-Disk -Number 1 -IsOffline $false
Set-Disk -Number 1 -IsReadOnly $false
Initialize-Disk -Number 1 -PartitionStyle GPT
New-Partition -DiskNumber 1 -UseMaximumSize -DriveLetter D
Format-Volume -DriveLetter D -FileSystem NTFS -NewFileSystemLabel 'Data' -Confirm:$false
```

Expected (last command):

```
DriveLetter FriendlyName FileSystemType DriveType HealthStatus OperationalStatus SizeRemaining    Size
----------- ------------ -------------- --------- ------------ ----------------- -------------    ----
D           Data         NTFS           Fixed     Healthy      OK                      4.93 GB 4.98 GB
```

GPT is the modern default (supports > 2 TiB and more partitions). If an RDP session is open, Windows may pop up "You need to format the disk" when the partition appears - ignore it; `Format-Volume` does that.

Equivalent in diskpart (for reference): `select disk 1`, `online disk`, `attributes disk clear readonly`, `convert gpt`, `create partition primary`, `format fs=ntfs label=Data quick`, `assign letter=D`.

## 5. Test file

```powershell
1..20000 | ForEach-Object { "line $_ $(Get-Random)" } | Set-Content D:\chg4615-test.txt
$hashBefore = (Get-FileHash D:\chg4615-test.txt -Algorithm SHA256).Hash
$hashBefore
```

## 6. Grow the data volume to 10 GiB

Workstation:

```bash
aws ec2 modify-volume --volume-id "$DATA_VOL" --size 10
aws ec2 describe-volumes-modifications --volume-ids "$DATA_VOL" \
  --query 'VolumesModifications[0].[ModificationState,Progress,OriginalSize,TargetSize]' --output text
```

```
modifying   0  5  10
optimizing  10 5  10        <- usable from here on
completed   100 5 10
```

Windows, once the state is `optimizing` or `completed`:

```powershell
Update-HostStorageCache
Get-Disk -Number 1 | Select-Object Number, @{n='SizeGiB';e={ $_.Size / 1GB }}, @{n='UnallocatedGiB';e={ [math]::Round(($_.Size - $_.AllocatedSize) / 1GB, 2) }}
$max = (Get-PartitionSupportedSize -DriveLetter D).SizeMax
Resize-Partition -DriveLetter D -Size $max
Get-Volume -DriveLetter D
```

```
Number SizeGiB UnallocatedGiB
------ ------- --------------
     1      10              5

DriveLetter FriendlyName FileSystemType ... SizeRemaining     Size
D           Data         NTFS           ...       9.93 GB  9.98 GB
```

`Update-HostStorageCache` is the PowerShell equivalent of diskpart `rescan` / Disk Management "Rescan Disks". If the size does not change yet, the EBS modification is still in `modifying`; wait and rescan.

## 7. Grow C: to 45 GiB

Workstation:

```bash
aws ec2 modify-volume --volume-id "$ROOT_VOL" --size 45
watch -n 15 "aws ec2 describe-volumes-modifications --volume-ids $ROOT_VOL --query 'VolumesModifications[0].[ModificationState,Progress]' --output text"
```

Windows:

```powershell
Update-HostStorageCache
Get-PartitionSupportedSize -DriveLetter C | Select-Object @{n='MinGB';e={[math]::Round($_.SizeMin/1GB,2)}}, @{n='MaxGB';e={[math]::Round($_.SizeMax/1GB,2)}}
$max = (Get-PartitionSupportedSize -DriveLetter C).SizeMax
Resize-Partition -DriveLetter C -Size $max
Get-Volume -DriveLetter C
```

```
MinGB MaxGB
----- -----
21.40 45.00

DriveLetter FriendlyName FileSystemType DriveType HealthStatus OperationalStatus SizeRemaining  Size
C                        NTFS           Fixed     Healthy      OK                     26.6 GB  45 GB
```

GUI alternative: Disk Management > right-click C: > Extend Volume. diskpart: `rescan`, `select volume C`, `extend`.

## 8. Validation

```powershell
Get-Volume -DriveLetter C, D | Format-Table DriveLetter, FileSystemLabel, FileSystem, HealthStatus,
    @{n='SizeGB';e={[math]::Round($_.Size/1GB,1)}}, @{n='FreeGB';e={[math]::Round($_.SizeRemaining/1GB,1)}}
Get-Disk | Format-Table Number, PartitionStyle, OperationalStatus, IsReadOnly, @{n='SizeGiB';e={$_.Size/1GB}}
(Get-FileHash D:\chg4615-test.txt -Algorithm SHA256).Hash -eq $hashBefore     # True
(Get-CimInstance Win32_OperatingSystem).LastBootUpTime                         # unchanged
```

Workstation:

```bash
aws ec2 describe-volumes-modifications --volume-ids "$ROOT_VOL" "$DATA_VOL" \
  --query 'VolumesModifications[].[VolumeId,ModificationState,TargetSize]' --output table
```

Persistence check: the drive letter `D:` and online state are stored on the disk/registry and survive a reboot. (Optional - only if you are allowed a reboot - `Restart-Computer` and confirm D: is still there.)

## 9. Rollback plan (for the change record)

- **D: wrong/corrupt:** Detach `xvdf`, create a volume from the CHG-4615 data snapshot in the same AZ (`aws ec2 create-volume --snapshot-id ... --availability-zone us-east-1a --volume-type gp3 --encrypted`), attach as `xvdf`, `Set-Disk -IsOffline $false`. The restored volume has the old size (5 GiB) and old contents.
- **C: problem / no boot:** EC2 console > instance > Actions > Monitor and troubleshoot > Replace root volume > choose the pre-change snapshot (instance ID, private IP and ENI stay the same), or `aws ec2 create-replace-root-volume-task --instance-id "$WIN_ID" --snapshot-id <snap>`.
- **Size itself:** cannot be reverted on the same volume. Accept, or migrate data to a new smaller volume.

## Cleanup / reset

The size increases are permanent for these volumes. To repeat the data-disk part of the exercise:

```powershell
Remove-Item D:\chg4615-test.txt
Clear-Disk -Number 1 -RemoveData -RemoveOEM -Confirm:$false      # Disk 1 only - check the number!
Set-Disk -Number 1 -IsOffline $true
Get-Disk -Number 1          # PartitionStyle RAW, Offline
```

To repeat the resize part you must wait 6 hours per volume, or redeploy the stack (which recreates 40/5 GiB volumes). Delete the snapshots when you no longer need them:

```bash
for S in $(aws ec2 describe-snapshots --owner-ids self --filters Name=tag:Change,Values=CHG-4615 --query 'Snapshots[].SnapshotId' --output text); do
  aws ec2 delete-snapshot --snapshot-id "$S"; done
```

## Common mistakes

- Assuming Windows disk numbers follow the EC2 device names - map by serial/volume ID.
- Running `Initialize-Disk` / `Clear-Disk` without `-Number` checks; on Disk 0 it is fatal.
- Trying to extend before the EBS modification leaves `modifying`, seeing no new space, and rebooting unnecessarily.
- `Resize-Partition -Size 45GB` - the max supported size is slightly smaller than the raw size; always use `SizeMax`. Running it when already at max returns "The size of the extent is less than the minimum of 1MB".
- Forgetting to make the disk writable (`IsReadOnly`) - `Initialize-Disk` fails with "The disk is write-protected" / "The media is write protected".
- Modifying size in two steps (45, then 50) and hitting the 6-hour cooldown.
- Not snapshotting because "extend is safe" - the snapshot also protects against picking the wrong disk.
- Choosing MBR for a data disk that might grow past 2 TiB.
