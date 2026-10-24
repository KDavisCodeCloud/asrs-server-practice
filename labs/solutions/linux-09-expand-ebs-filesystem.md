# Solution — Linux 09: Expand an EBS volume and the Linux filesystem

This solution uses XFS on a GPT-partitioned data volume (the path that exercises every
step). The ext4 and unpartitioned variants are noted inline.

## 0. Prepare the data volume

Identify the device. On Nitro instances EBS appears as NVMe; the device serial is the
volume ID without the dash:

```bash
lsblk -o NAME,SIZE,TYPE,FSTYPE,SERIAL,MOUNTPOINT
```

```
NAME          SIZE TYPE FSTYPE SERIAL               MOUNTPOINT
nvme0n1        20G disk        vol0a1b2c3d4e5f60789
├─nvme0n1p1    20G part xfs                         /
├─nvme0n1p127   1M part
└─nvme0n1p128  10M part vfat                        /boot/efi
nvme1n1         5G disk        vol0fedcba9876543210
```

Compare to the `LinuxDataVolumeId` output (e.g. `vol-0fedcba9876543210`). Device names
(`nvme1n1`) can change between boots; use `/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol0fedcba9876543210`
or the filesystem UUID, never the bare name, in config.

```bash
DEV=/dev/nvme1n1
sudo wipefs -n $DEV          # confirm empty: no output
sudo parted -s $DEV mklabel gpt mkpart data xfs 1MiB 100%
lsblk $DEV
sudo mkfs.xfs -L asrsdata ${DEV}p1
sudo mkdir -p /data
UUID=$(sudo blkid -s UUID -o value ${DEV}p1)
echo "UUID=$UUID /data xfs defaults,nofail 0 2" | sudo tee -a /etc/fstab
findmnt --verify
sudo mount -a
df -hT /data
# /dev/nvme1n1p1 xfs  5.0G   68M  4.9G   2% /data
```

ext4 variant: `sudo mkfs.ext4 -L asrsdata ${DEV}p1` and `ext4` in fstab.
Unpartitioned variant: `mkfs.xfs $DEV` directly; there is then no `growpart` step.

Fill to ~90% and record checksums:

```bash
sudo mkdir -p /data/payload
for i in $(seq 1 8); do sudo dd if=/dev/urandom of=/data/payload/blob$i bs=1M count=520 status=none; done
df -h /data
# /dev/nvme1n1p1  5.0G  4.2G  819M  84% /data   (add a blob or two to reach ~90%)
cd /data/payload && sudo sha256sum blob* | sudo tee /root/CHG-3342.sha256 >/dev/null; cd -
```

## 1. Snapshot first (workstation)

```bash
VOL=vol-0fedcba9876543210
SNAP=$(aws ec2 create-snapshot --volume-id $VOL \
  --description "CHG-3342 pre-resize asrs-lab-linux data" \
  --tag-specifications 'ResourceType=snapshot,Tags=[{Key=Change,Value=CHG-3342}]' \
  --query SnapshotId --output text)
aws ec2 wait snapshot-completed --snapshot-ids $SNAP
aws ec2 describe-snapshots --snapshot-ids $SNAP --query 'Snapshots[0].State'
# "completed"
```

## 2. Modify the volume

```bash
aws ec2 modify-volume --volume-id $VOL --size 8
aws ec2 describe-volumes-modifications --volume-ids $VOL \
  --query 'VolumesModifications[0].[ModificationState,OriginalSize,TargetSize,Progress]'
# ["modifying", 5, 8, 0]
# ... later
# ["optimizing", 5, 8, 40]
```

The new size is usable by the OS as soon as the state reaches `optimizing`; `completed`
can take much longer. Console: EC2 → Volumes → select → Actions → Modify volume.

## 3. OS sees the new size

```bash
lsblk /dev/nvme1n1
# NAME        SIZE TYPE
# nvme1n1       8G disk
# └─nvme1n1p1   5G part /data
```

The NVMe driver picks up the change automatically; no rescan needed on Nitro instances.

## 4. Grow the partition

`growpart` is in `cloud-utils-growpart`, installed on AL2023 AMIs. Note the space between
device and partition number:

```bash
sudo growpart /dev/nvme1n1 1
# CHANGED: partition=1 start=2048 old: size=10481664 end=10483712 new: size=16775135 end=16777183
lsblk /dev/nvme1n1
# └─nvme1n1p1   8G part /data
```

`NOCHANGE: partition 1 is size ... it cannot be grown` means the volume modification has not
reached the OS yet, or you already grew it.

## 5. Grow the filesystem online

XFS — takes the **mount point**:

```bash
sudo xfs_growfs -d /data
```

```
meta-data=/dev/nvme1n1p1         isize=512    agcount=4, agsize=327552 blks
...
data blocks changed from 1310208 to 2096891
```

ext4 — takes the **device**:

```bash
sudo resize2fs /dev/nvme1n1p1
# Filesystem at /dev/nvme1n1p1 is mounted on /data; on-line resizing required
# The filesystem on /dev/nvme1n1p1 is now 2096891 (4k) blocks long.
```

## 6. Verify

```bash
df -hT /data
# /dev/nvme1n1p1 xfs  8.0G  4.6G  3.5G  57% /data
findmnt /data
cd /data/payload && sha256sum -c /root/CHG-3342.sha256; cd -
# blob1: OK
# ...
```

## 7. Stretch: root volume 20 → 25 GiB

```bash
IID=i-0123456789abcdef0      # LinuxInstanceId output
ROOT_VOL=$(aws ec2 describe-instances --instance-ids $IID \
  --query "Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName=='/dev/xvda'].Ebs.VolumeId" --output text)
aws ec2 create-snapshot --volume-id $ROOT_VOL --description "CHG-3342 root pre-resize"   # wait for completed
aws ec2 modify-volume --volume-id $ROOT_VOL --size 25
```

On the instance (root filesystem is partition **1**; 127 = BIOS boot, 128 = EFI):

```bash
lsblk /dev/nvme0n1
sudo growpart /dev/nvme0n1 1
sudo xfs_growfs -d /
df -h /
# /dev/nvme0n1p1   25G  2.4G   23G  10% /
```

## 8. Constraints to state

- **No shrinking**: `modify-volume` rejects a smaller size. To "shrink", create a smaller volume,
  copy data (`rsync -aHAX`), and swap. XFS cannot be shrunk at all; ext4 only offline.
- **Cooldown**: after a modification you must wait at least 6 hours (and for the previous
  modification to finish) before modifying the same volume again.
- **CloudFormation drift**: the stack still declares 5 GiB (and 20 GiB root). Run drift
  detection:
  ```bash
  aws cloudformation detect-stack-drift --stack-name <nested stack name>
  ```
  The volume shows `MODIFIED` on `Size`. A later template change that tries to set 5 GiB fails.
  Either update the template to the new size or accept drift until the lab is destroyed.

## Cleanup / reset

To repeat from a raw 5 GiB volume, the simplest route is destroy + redeploy the stack. To keep
the stack:

```bash
sudo umount /data
sudo sed -i '\| /data xfs |d' /etc/fstab
sudo wipefs -a /dev/nvme1n1p1 && sudo wipefs -a /dev/nvme1n1
sudo rmdir /data
sudo rm -f /root/CHG-3342.sha256
```

The volume stays 8 GiB (cannot shrink). Delete snapshots when done:

```bash
aws ec2 delete-snapshot --snapshot-id $SNAP
```

## Common mistakes

- `growpart /dev/nvme1n1p1` (partition path) instead of `growpart /dev/nvme1n1 1`.
- Running `xfs_growfs` on an ext4 filesystem or `resize2fs` on XFS
  (`resize2fs: Bad magic number in super-block`).
- `xfs_growfs /dev/nvme1n1p1` on older xfsprogs — it expects the mount point.
- Growing the filesystem before the partition: nothing changes.
- Putting `/dev/nvme1n1` in fstab; names can swap between boots.
- Omitting `nofail` and then having a boot wait on a detached volume.
- Skipping the snapshot, or not waiting for it to complete.
- Trying a second resize within 6 hours.
