# Solution — AWS Lab 04: Create and Restore EBS Snapshots

Conventions: local commands use `--profile asrs-admin --region us-east-1`; OS commands run in an SSM session on `asrs-lab-linux` (`sudo` required).

## 0. Gather IDs

```bash
out() { aws cloudformation describe-stacks --stack-name asrs-lab \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text \
  --profile asrs-admin --region us-east-1; }
LINUX_ID=$(out LinuxInstanceId); DATA_VOL=$(out LinuxDataVolumeId)
AZ=$(aws ec2 describe-instances --instance-ids "$LINUX_ID" \
  --query "Reservations[0].Instances[0].Placement.AvailabilityZone" --output text --profile asrs-admin --region us-east-1)

aws ec2 describe-volumes --volume-ids "$DATA_VOL" \
  --query "Volumes[0].[State,Size,AvailabilityZone,Encrypted,Attachments[0].InstanceId,Attachments[0].Device]" \
  --profile asrs-admin --region us-east-1
# ["in-use", 5, "us-east-1a", true, "i-0abc...", "/dev/sdf"]
```
If `State` is `available` (not attached), the template creates the volume but not the attachment: attach it with `aws ec2 attach-volume --volume-id "$DATA_VOL" --instance-id "$LINUX_ID" --device /dev/sdf` (note this is an out-of-band change).

## 1. Map device to volume (instance)

On Nitro, `/dev/sdf` appears as `/dev/nvmeXn1`; the NVMe serial is the volume ID without the dash.

```bash
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,SERIAL
# nvme0n1      8G disk               vol0aaa...   (root, partitions nvme0n1p1 ...)
# nvme1n1      5G disk               vol0bbb...   (data, no FSTYPE = raw)
ls -l /dev/disk/by-id/ | grep -i vol
```
Confirm `SERIAL` matches `$DATA_VOL` (`vol-0bbb...` -> `vol0bbb...`) before formatting.

## 2. Filesystem and test data (instance)

```bash
DEV=/dev/nvme1n1                      # the one you verified
sudo file -s $DEV                     # "data" = no filesystem; anything else -> STOP
sudo mkfs.xfs -L asrsdata $DEV
sudo mkdir -p /data && sudo mount $DEV /data
echo "ASRS inventory baseline $(date -u +%FT%TZ)" | sudo tee /data/inventory.txt
sha256sum /data/inventory.txt | tee /tmp/orig.sha256
sync
sudo fsfreeze -f /data                # optional: quiesce for a consistent snapshot
```
`fsfreeze` blocks writes; run the next step immediately and unfreeze after `create-snapshot` returns (the point in time is captured at the API call, not at completion).

## 3. Snapshot (local)

```bash
SNAP=$(aws ec2 create-snapshot --volume-id "$DATA_VOL" \
  --description "CHG-5531 pre-upgrade data volume asrs-lab-linux" \
  --tag-specifications 'ResourceType=snapshot,Tags=[{Key=Project,Value=asrs-lab},{Key=Name,Value=lab04-linux-data-preupgrade},{Key=Change,Value=CHG-5531}]' \
  --query SnapshotId --output text --profile asrs-admin --region us-east-1)
echo $SNAP
```
Instance: `sudo fsfreeze -u /data`

```bash
aws ec2 wait snapshot-completed --snapshot-ids "$SNAP" --profile asrs-admin --region us-east-1
aws ec2 describe-snapshots --snapshot-ids "$SNAP" \
  --query "Snapshots[0].[State,Progress,Encrypted,KmsKeyId,VolumeSize]" --profile asrs-admin --region us-east-1
# ["completed", "100%", true, "arn:aws:kms:...", 5]
```

## 4. Simulate the bad upgrade (instance)

```bash
echo "CORRUPTED BY UPGRADE" | sudo tee /data/inventory.txt
sha256sum -c /tmp/orig.sha256        # FAILED
```

## 5. Restore to a new volume and attach (local)

```bash
RVOL=$(aws ec2 create-volume --snapshot-id "$SNAP" --availability-zone "$AZ" --volume-type gp3 \
  --tag-specifications 'ResourceType=volume,Tags=[{Key=Project,Value=asrs-lab},{Key=Name,Value=lab04-restore}]' \
  --query VolumeId --output text --profile asrs-admin --region us-east-1)
aws ec2 wait volume-available --volume-ids "$RVOL" --profile asrs-admin --region us-east-1
aws ec2 attach-volume --volume-id "$RVOL" --instance-id "$LINUX_ID" --device /dev/sdg \
  --profile asrs-admin --region us-east-1
aws ec2 wait volume-in-use --volume-ids "$RVOL" --profile asrs-admin --region us-east-1
```
Encryption and KMS key are inherited from the snapshot. The AZ must match the instance's AZ or `attach-volume` fails with `InvalidVolume.ZoneMismatch`.

## 6. Mount read-only and verify (instance)

```bash
lsblk -o NAME,SIZE,FSTYPE,LABEL,SERIAL     # new nvme2n1 with LABEL asrsdata, SERIAL = restored vol id
sudo mkdir -p /restore
sudo mount -o ro /dev/nvme2n1 /restore
# mount: /restore: wrong fs type, bad option, bad superblock ...
sudo dmesg | tail -3
# XFS (nvme2n1): Filesystem has duplicate UUID <uuid> - can't mount
sudo mount -o ro,nouuid /dev/nvme2n1 /restore
cat /restore/inventory.txt                       # baseline content
(cd /restore && sha256sum inventory.txt) ; cat /tmp/orig.sha256   # hashes equal
cat /data/inventory.txt                          # still "CORRUPTED BY UPGRADE"
```
The restored filesystem is a block copy, so it has the same XFS UUID as `/data`. `nouuid` lets XFS mount it. For a permanent second copy you would run `sudo xfs_admin -U generate /dev/nvme2n1` (unmounted, read-write) instead. ext4 does not refuse duplicate UUIDs, but `/etc/fstab` entries by UUID/label become ambiguous.

File-level recovery would now be `sudo cp /restore/inventory.txt /data/`. Do it, then `sha256sum -c /tmp/orig.sha256` passes.

## 7. Advanced — root-volume replacement

Snapshot the root volume, write a marker, then roll back:

```bash
ROOT_VOL=$(aws ec2 describe-instances --instance-ids "$LINUX_ID" \
  --query "Reservations[0].Instances[0].BlockDeviceMappings[?DeviceName=='/dev/xvda'].Ebs.VolumeId" \
  --output text --profile asrs-admin --region us-east-1)
ROOT_SNAP=$(aws ec2 create-snapshot --volume-id "$ROOT_VOL" --description "lab04 root before change" \
  --tag-specifications 'ResourceType=snapshot,Tags=[{Key=Project,Value=asrs-lab},{Key=Name,Value=lab04-linux-root}]' \
  --query SnapshotId --output text --profile asrs-admin --region us-east-1)
aws ec2 wait snapshot-completed --snapshot-ids "$ROOT_SNAP" --profile asrs-admin --region us-east-1
```
(If the root device name differs, read it from `Reservations[0].Instances[0].RootDeviceName`.)

Instance: `echo "post-snapshot change" | sudo tee /root/marker.txt`. Unmount `/restore` first (step 8) so the reboot is clean.

```bash
TASK=$(aws ec2 create-replace-root-volume-task --instance-id "$LINUX_ID" --snapshot-id "$ROOT_SNAP" \
  --tag-specifications 'ResourceType=replace-root-volume-task,Tags=[{Key=Project,Value=asrs-lab}]' \
  --query ReplaceRootVolumeTask.ReplaceRootVolumeTaskId --output text --profile asrs-admin --region us-east-1)
aws ec2 describe-replace-root-volume-tasks --replace-root-volume-task-ids "$TASK" \
  --query "ReplaceRootVolumeTasks[0].TaskState" --profile asrs-admin --region us-east-1
# pending -> in-progress -> succeeded  (a few minutes; instance reboots, must be running)
```
Results: same instance ID, private IP, ENI, instance profile and data volume; new root volume ID; `/root/marker.txt` no longer exists; the instance is back Online in SSM after the reboot. The **old root volume is detached and retained** (tagged by AWS with the task) — find and delete it:

```bash
aws ec2 describe-volumes --filters Name=status,Values=available Name=availability-zone,Values="$AZ" \
  --query "Volumes[].[VolumeId,Size,CreateTime,Tags]" --profile asrs-admin --region us-east-1
```
Alternatives: omit `--snapshot-id` to restore to the launch state, use `--image-id` to swap to a different AMI, or pass `--delete-replaced-root-volume` to delete the old one automatically. `/data` must be remounted after the reboot (it is not in fstab).

## 8. Cleanup

Instance:
```bash
sudo umount /restore 2>/dev/null; sudo umount /data 2>/dev/null
```
Optionally wipe the filesystem signature so the data volume is raw again (as the template delivered it): `sudo wipefs -a /dev/nvme1n1` — verify the device first.

Local:
```bash
aws ec2 detach-volume --volume-id "$RVOL" --profile asrs-admin --region us-east-1
aws ec2 wait volume-available --volume-ids "$RVOL" --profile asrs-admin --region us-east-1
aws ec2 delete-volume --volume-id "$RVOL" --profile asrs-admin --region us-east-1
aws ec2 delete-volume --volume-id <old-root-volume-id> --profile asrs-admin --region us-east-1   # from step 7

for S in $(aws ec2 describe-snapshots --owner-ids self \
    --filters Name=tag:Project,Values=asrs-lab Name=tag:Name,Values='lab04-*' \
    --query "Snapshots[].SnapshotId" --output text --profile asrs-admin --region us-east-1); do
  aws ec2 delete-snapshot --snapshot-id "$S" --profile asrs-admin --region us-east-1
done
```
Note: a snapshot that is the source of an in-flight replace-root task or registered AMI cannot be deleted until that is finished/deregistered.

Verify nothing is left:
```bash
aws ec2 describe-snapshots --owner-ids self --filters Name=tag:Project,Values=asrs-lab \
  --query "Snapshots[].[SnapshotId,Tags[?Key=='Name']|[0].Value]" --profile asrs-admin --region us-east-1
aws ec2 describe-volumes --filters Name=tag:Name,Values=lab04-restore --profile asrs-admin --region us-east-1
```
Drift check on the Storage nested stack (same pattern as Lab 01, `contains(LogicalResourceId,'Storage')`) should be `IN_SYNC`.

## Data Lifecycle Manager (talking point)

DLM automates snapshot schedules by **target tag**: e.g. every 24 h at 03:00 UTC for volumes tagged `Backup=daily`, retain 7, copy tags, optional cross-Region copy and fast snapshot restore. It needs the `AWSDataLifecycleManagerDefaultRole` service role (`aws dlm create-default-role`). For multi-service, cross-account backup with vault lock and reporting, AWS Backup is the broader answer. In this lab, defining DLM in CloudFormation (`AWS::DLM::LifecyclePolicy`) would keep it under IaC — but snapshots it creates still outlive the stack.

## Common mistakes

- Formatting the wrong NVMe device (device order is not guaranteed; always match SERIAL to volume ID).
- Creating the restore volume in a different AZ.
- Mounting the restored XFS volume without `nouuid` and assuming the snapshot is corrupt.
- Mounting the restore read-write and "restoring" in the wrong direction.
- Snapshotting without `sync`/quiesce and expecting application consistency.
- Adding `/data` to fstab without `nofail` — a missing volume then drops the instance into emergency mode, unreachable via SSM.
- Forgetting the replaced root volume and untagged snapshots; `destroy.yml` does not remove them.
