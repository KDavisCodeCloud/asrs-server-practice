# Teardown

## 1. Destroy the lab (normal path)

Actions → **destroy** → Run workflow → type the stack name (`asrs-lab`) → approve the
`lab-destroy` environment.

The workflow:
1. Checks the typed name matches. If not, nothing happens.
2. Records which EBS volumes were attached, so it can look for snapshots taken from them.
3. Runs `delete-stack` and waits. The nested stacks are deleted in reverse dependency order:
   data volumes, instances, security groups and KMS key, IAM and network.
4. Writes a **retained resources report** to the run summary, listing:
   - snapshots tagged `Project=asrs-lab`, and snapshots taken from the lab's volumes;
   - volumes, AMIs and Elastic IPs tagged `Project=asrs-lab`.

Empty tables mean nothing was left. Anything listed is still being billed.

From your machine instead:

```bash
aws cloudformation delete-stack --stack-name asrs-lab --profile asrs-admin
aws cloudformation wait stack-delete-complete --stack-name asrs-lab --profile asrs-admin
```

## 2. Things the stack does not delete

| Left behind | Why | Remove with |
|---|---|---|
| Snapshots you took | Snapshots outlive their volumes by design | `aws ec2 delete-snapshot --snapshot-id snap-...` |
| Volumes you created from snapshots | Not part of the stack | `aws ec2 delete-volume --volume-id vol-...` (detach first) |
| AMIs you created | Not part of the stack | `aws ec2 deregister-image`, then delete its snapshots |
| Security-group rules or IAM policies you added by hand | Usually removed with the group/role. Rules that **reference** a lab SG from a non-lab SG block deletion | Remove the rule, retry the destroy |
| Customer-managed KMS key | KMS waits before deleting (`PendingWindowInDays: 7`) | Nothing to do. It shows as *Pending deletion*. Volumes or snapshots still encrypted with it become unreadable after deletion |
| Session Manager logging bucket / log group, if you configured one | Created by you | Delete in S3 / CloudWatch Logs |
| Budget, Cost Explorer settings | Account-level, free | Keep, or delete in Billing |

Find stray tagged resources:

```bash
aws resourcegroupstaggingapi get-resources --tag-filters Key=Project,Values=asrs-lab \
  --query "ResourceTagMappingList[].ResourceARN" --output text --profile asrs-admin
aws ec2 describe-snapshots --owner-ids self --query "Snapshots[].[SnapshotId,VolumeId,StartTime,Description]" \
  --output table --profile asrs-admin
aws ec2 describe-volumes --filters Name=status,Values=available --output table --profile asrs-admin
```

Untagged resources you created by hand in the console are not found by tag searches. Use the last
two commands, and check **EC2 → Elastic IPs**.

## 3. When deletion fails

`DELETE_FAILED` almost always means something outside the stack depends on something inside it.

| Symptom (stack events) | Cause | Fix |
|---|---|---|
| Security group `has a dependent object` | An ENI you created, or a rule in another SG referencing it | Delete the ENI or rule, then destroy again |
| VPC `has dependencies` | An instance, ENI, endpoint or subnet you created by hand in the lab VPC | Delete it, destroy again |
| Subnet `has dependencies` | Same, at subnet level (e.g. a Reachability Analyzer or Lambda ENI) | Same |
| IAM role `must detach all policies first` | You attached a managed policy by hand | Detach it, destroy again |
| Volume `is in use` / attachment timeout | Volume attached by hand | Detach it, destroy again |

Look at the **nested** stack's events. The parent only says `Embedded stack ... was not
successfully deleted`. As a last resort, `delete-stack --retain-resources <LogicalId>` skips the
blocking resource. You must then delete it by hand, or you will keep paying for it.

## 4. Remove the bootstrap (only when you are completely finished)

The bootstrap costs nothing worth mentioning. Remove it only when you are retiring the lab:

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name asrs-lab-bootstrap --profile asrs-admin \
  --query "Stacks[0].Outputs[?OutputKey=='ArtifactBucketName'].OutputValue" --output text)
aws s3 rm "s3://$BUCKET" --recursive --profile asrs-admin      # the bucket must be empty first
aws cloudformation delete-stack --stack-name asrs-lab-bootstrap --profile asrs-admin
aws cloudformation wait stack-delete-complete --stack-name asrs-lab-bootstrap --profile asrs-admin
```

Delete the lab stack **before** the bootstrap. The lab stack is managed through
`asrs-lab-cfn-execution`, and CloudFormation cannot delete a stack whose service role no longer
exists. If you do it the wrong way round, recreate the bootstrap, delete the lab, then delete the
bootstrap.

If the OIDC provider existed before (`CreateOidcProvider=false`), it stays. Then remove the GitHub
Environments and repository variables, or the whole repository.
