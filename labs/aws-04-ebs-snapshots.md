# AWS Lab 04 — Create and Restore EBS Snapshots

## Objective

Take a point-in-time snapshot of a data volume, restore it to a new volume, attach and mount it alongside the original to verify contents, and (advanced) replace an instance's root volume from a snapshot. Leave no billable snapshots or volumes behind.

## Scenario

> **Change CHG-5531 (Normal):** Before a vendor upgrade on `asrs-lab-linux`, the storage team requires a restorable backup of the data volume and a tested restore ("a backup you have not restored is not a backup"). Produce the snapshot, prove a file-level restore by mounting the restored copy read-only next to the live volume, and record the snapshot ID in the change. Also rehearse a root-volume rollback for the OS disk.

## Prerequisites

- Stack `asrs-lab` deployed with the optional data volumes enabled (stack output `LinuxDataVolumeId` is not empty). The volume is 5 GiB, encrypted, **raw/unformatted**.
- Admin session `--profile asrs-admin`; Linux instance online in Session Manager.
- You know the instance's Availability Zone (volumes attach only within the same AZ).
- Snapshots of encrypted volumes are encrypted with the same KMS key; restored volumes are encrypted too.

## What you need to accomplish

1. Identify the data volume and how it appears inside the OS (Nitro NVMe device naming; map device to volume ID).
2. Create a filesystem on the data volume, mount it, write a test file with known content and a checksum. Make sure the data is flushed before you snapshot.
3. Create a snapshot of the data volume with tags `Project=asrs-lab` and a meaningful `Name`, and wait until it is `completed`.
4. Change or delete the test file on the live volume (simulate the "bad upgrade").
5. Create a new volume from the snapshot in the instance's AZ, tag it, attach it as an additional device, and mount it **read-only** at a separate path. Handle any filesystem-identity conflict with the original.
6. Prove the restored file matches the original checksum, and that the live volume still shows the changed state.
7. Advanced: snapshot the Linux root volume, then use a root-volume replacement task to roll the root disk back to that snapshot without changing the instance ID, private IP, or attached data volume. Explain what happens to the old root volume.
8. Clean up every snapshot and volume you created; leave the template-managed data volume in place.
9. Be ready to explain how you would automate this with Data Lifecycle Manager.

## Commands or tools to research

- `lsblk`, `nvme id-ctrl` / `/dev/disk/by-id/`, `mkfs.xfs`, `mount -o ro`, `xfs_admin`, `sync`, `fsfreeze`, `sha256sum`
- `aws ec2 describe-volumes`, `create-snapshot`, `describe-snapshots`, `wait snapshot-completed`
- `aws ec2 create-volume`, `attach-volume`, `wait volume-available`, `detach-volume`, `delete-volume`, `delete-snapshot`
- `aws ec2 create-replace-root-volume-task`, `describe-replace-root-volume-tasks`
- `aws dlm create-lifecycle-policy` (talking point)
- Console: EC2 > Elastic Block Store > Volumes / Snapshots / Lifecycle Manager

## Validation criteria

- Snapshot state `completed`, `Encrypted: true`, tags `Project=asrs-lab` and `Name` present.
- Restored volume is in the same AZ, attached as a second data device, mounted read-only; `sha256sum` of the restored file equals the original.
- Live volume shows the "bad upgrade" state; nothing on it was overwritten by the restore.
- (Advanced) Replace-root-volume task `succeeded`; instance ID and private IP unchanged; a marker file written after the root snapshot is gone; data volume still attached.
- After cleanup: `describe-snapshots --owner-ids self --filters Name=tag:Project,Values=asrs-lab` and a volume query for your restore tag both return empty (except anything the template created).

## Recovery considerations

- **destroy.yml does not delete snapshots** (they are not stack resources). It reports `Project=asrs-lab` snapshots it finds, which is why tagging matters. Untagged snapshots are easy to forget and bill indefinitely (standard tier, per GB-month of changed blocks).
- Volumes you create by hand are not stack resources either: an attached extra volume is just detached when an instance is replaced/terminated, and keeps billing.
- The replaced root volume from a root-volume replacement task is **kept** by default and billed; delete it (or use the delete option on the task).
- Attaching extra volumes and replacing root volumes does not change CloudFormation-managed properties, but formatting/mounting is OS state CloudFormation does not track. Do not add a mount to `/etc/fstab` without `nofail`, or the next boot can hang in emergency mode — and you only have SSM.
- Never `mkfs` the wrong device. Map device to volume ID before formatting.
- Run `aws cloudformation detect-stack-drift` on the Storage nested stack at the end; it should be `IN_SYNC`.

## Interview angle

"Snapshots are incremental, crash-consistent block copies stored regionally; restore means create-volume in a chosen AZ and lazy-load from S3 (initialize or use Fast Snapshot Restore for performance). For app consistency, quiesce/flush or use VSS on Windows."

[Solution](solutions/aws-04-ebs-snapshots.md) — try it first.
