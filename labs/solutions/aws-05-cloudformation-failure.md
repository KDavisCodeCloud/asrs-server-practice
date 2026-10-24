# Solution — AWS Lab 05: Troubleshoot a CloudFormation Deployment Failure

Conventions: local commands use `--profile asrs-admin --region us-east-1`. Names used below match this repo:
- `deploy.yml` inputs: `linux_instance_type`, `windows_root_gib`;
- `main.yaml` parameters: `LinuxInstanceType` (AllowedPattern `^[a-z][a-z0-9-]*\.[a-z0-9]+$`, so `t3.nonexistent` passes the pattern and fails in EC2) and `WindowsRootVolumeSize` (MinValue 30);
- nested-stack logical IDs: `Network`, `Security`, `Iam`, `Linux`, `Windows`, `Storage`. Inside `Linux` and `Windows` the instance's logical ID is `Instance`.

## 1. Would anything have caught it?

```bash
cd "/mnt/c/Users/Kelvin/projects/ASRS Server practice"
cfn-lint cloudformation/*.yaml
```
`cfn-lint` validates literal property values against the resource spec (e.g. an invalid `InstanceType` literal is flagged), but a value passed in as a **parameter at deploy time** is not visible to it. What does catch it:
- a parameter `AllowedValues` / `AllowedPattern` / `MinValue` in the template. If present, the deploy fails immediately with `ValidationError: Parameter 'LinuxInstanceType' must be one of AllowedValues` — **no stack events, stack unchanged**. That is the cheapest guard.
- a pre-flight in the workflow:
  ```bash
  aws ec2 describe-instance-type-offerings --location-type availability-zone \
    --filters Name=instance-type,Values=t3.nonexistent --profile asrs-admin --region us-east-1
  # {"InstanceTypeOfferings": []}  -> not offered
  aws ec2 describe-images --image-ids <windows-ami-id> \
    --query "Images[0].BlockDeviceMappings[0].Ebs.VolumeSize" --profile asrs-admin --region us-east-1
  # 30   -> root volume must be >= 30 GiB
  ```
`aws cloudformation validate-template` only checks syntax/structure, not values.

## 2. Run the failing deploy

GitHub > Actions > `deploy.yml` > Run workflow > set the bad input > approve in environment `lab` (or `gh workflow run deploy.yml -f <input>=t3.nonexistent`). The job fails at the CloudFormation wait step.

```bash
aws cloudformation describe-stacks --stack-name asrs-lab \
  --query "Stacks[0].[StackStatus,StackStatusReason]" --profile asrs-admin --region us-east-1
# ["UPDATE_ROLLBACK_COMPLETE", null]

aws cloudformation describe-stack-events --stack-name asrs-lab \
  --query "StackEvents[?contains(ResourceStatus,'FAILED')].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]" \
  --output table --profile asrs-admin --region us-east-1
# Linux    | UPDATE_FAILED | Embedded stack arn:aws:cloudformation:...:stack/asrs-lab-Linux-XXXX/... was not successfully updated. Currently in UPDATE_ROLLBACK_IN_PROGRESS with reason: The following resource(s) failed to update: [Instance].
# asrs-lab | UPDATE_ROLLBACK_IN_PROGRESS | The following resource(s) failed to update: [Linux].
```
The parent only reports that a child failed. You may also see other nested stacks with `Resource update cancelled` — those are victims, not causes.

## 3. Find the root cause in the nested stack

```bash
aws cloudformation describe-stack-resources --stack-name asrs-lab \
  --query "StackResources[?ResourceType=='AWS::CloudFormation::Stack'].[LogicalResourceId,ResourceStatus,PhysicalResourceId]" \
  --output table --profile asrs-admin --region us-east-1

NESTED=<PhysicalResourceId of the Linux (or Windows) nested stack>
aws cloudformation describe-stack-events --stack-name "$NESTED" \
  --query "reverse(StackEvents[?contains(ResourceStatus,'FAILED')])[0].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]" \
  --profile asrs-admin --region us-east-1
```
Events are newest first; `reverse(...)[0]` gives the **earliest** failure = the root cause. Expected reasons (wording varies):

- Failure A (update of existing instance): `Instance UPDATE_FAILED  Invalid value 't3.nonexistent' for instanceType. (Service: Ec2, Status Code: 400, ...)`. `InstanceType` is an "update with some interruption" property: CloudFormation **stopped** the instance, the modify call failed, rollback started it again.
- Failure B (Windows root size 8): in this repo it never reaches EC2. `WindowsRootVolumeSize` has `MinValue: 30`, so the `aws cloudformation deploy` step fails at once with `ValidationError ... Parameter 'WindowsRootVolumeSize' must be a number not less than 30`. There are **no stack events** and the stack status doesn't change. That is the cheapest guard working. Without the guard, EC2 would have rejected it as `Instance CREATE_FAILED  Volume of size 8GB is smaller than snapshot 'snap-...', expect size >= 30GB`. That would be `CREATE_FAILED` even during a stack *update*, because `BlockDeviceMappings` changes require **replacement**, so CloudFormation tries to create a new instance first.

Console: CloudFormation > Stacks > toggle **View nested** > select the nested stack > Events > filter "Failed".

Bulk view of all nested failures:
```bash
for S in $(aws cloudformation describe-stack-resources --stack-name asrs-lab \
    --query "StackResources[?ResourceType=='AWS::CloudFormation::Stack'].PhysicalResourceId" --output text \
    --profile asrs-admin --region us-east-1); do
  aws cloudformation describe-stack-events --stack-name "$S" --max-items 50 \
    --query "StackEvents[?contains(ResourceStatus,'FAILED') && !contains(ResourceStatusReason,'cancelled')].[StackName,LogicalResourceId,ResourceStatusReason]" \
    --output text --profile asrs-admin --region us-east-1
done
```

For a **create** failure (Failure C) the nested stacks are deleted during rollback, so `describe-stack-resources` shows nothing useful. Find them by status and query by stack **ID** (events stay retrievable ~90 days):
```bash
aws cloudformation list-stacks --stack-status-filter DELETE_COMPLETE \
  --query "StackSummaries[?contains(StackName,'asrs-lab')].[StackName,StackId,DeletionTime]" --output table \
  --profile asrs-admin --region us-east-1
aws cloudformation describe-stack-events --stack-name <StackId-arn> --profile asrs-admin --region us-east-1
```

## 4. Stack states

| State | Meaning | Next allowed action |
|---|---|---|
| `UPDATE_ROLLBACK_COMPLETE` | Update failed, rolled back to last good state | Normal: fix input and update again |
| `ROLLBACK_COMPLETE` | **Create** failed and rolled back; stack is an empty shell | Only delete. `Stack is in ROLLBACK_COMPLETE state and can not be updated` on any update |
| `ROLLBACK_FAILED` | Create rollback could not delete something | Delete (optionally `--retain-resources`) |
| `UPDATE_ROLLBACK_FAILED` | Rollback of an update itself failed (e.g. a resource was changed/deleted out of band, a dependency blocks it) | `continue-update-rollback`, optionally skipping resources |
| `DELETE_FAILED` | Delete blocked (dependency, permission, non-empty resource) | Remove the blocker and delete again, or `delete-stack --retain-resources` |

Create-time option worth knowing: `--on-failure DO_NOTHING` / `--disable-rollback` keeps failed resources for inspection; `rollback-stack` rolls it back later.

## 5. Why Failure B is dangerous even with a valid value

Changing `BlockDeviceMappings` on `AWS::EC2::Instance` = **replacement**: new instance ID, new private IP, new root disk built from the AMI, old instance terminated. Anything on the old root volume is gone, SSM registration changes, and any data volume attachment moves. A change set shows this up front (`Replacement: True`). Root volume growth on a live instance should be done with `aws ec2 modify-volume` + OS extend, then the template aligned — or by accepting replacement deliberately.

## 6. Preview with a change set

The stack's service role is reused automatically for operations started by your admin profile. Build parameters that keep everything as-is except the one you change:

```bash
PARAMS=$(aws cloudformation describe-stacks --stack-name asrs-lab --query "Stacks[0].Parameters[].ParameterKey" \
  --output text --profile asrs-admin --region us-east-1 | tr '\t' '\n' \
  | awk '$1=="LinuxInstanceType"{print "ParameterKey=LinuxInstanceType,ParameterValue=t3.small"; next}
         {print "ParameterKey="$1",UsePreviousValue=true"}')

aws cloudformation create-change-set --stack-name asrs-lab --change-set-name lab05-preview \
  --use-previous-template --include-nested-stacks \
  --parameters $PARAMS \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --profile asrs-admin --region us-east-1
aws cloudformation wait change-set-create-complete --stack-name asrs-lab --change-set-name lab05-preview \
  --profile asrs-admin --region us-east-1
aws cloudformation describe-change-set --stack-name asrs-lab --change-set-name lab05-preview \
  --query "Changes[].ResourceChange.[LogicalResourceId,Action,Replacement,ChangeSetId]" \
  --output table --profile asrs-admin --region us-east-1
```
Top level shows the nested stack `Modify`; follow its `ChangeSetId` with `describe-change-set --change-set-name <arn>` to see `Instance  Modify  False` (resize, with interruption). Repeat with the Windows root size parameter (`40` -> `45`) to see `Instance  Modify  True` (replacement) in the `Windows` nested change set. Delete the change set instead of executing it.

Delete without executing:
```bash
aws cloudformation delete-change-set --stack-name asrs-lab --change-set-name lab05-preview \
  --profile asrs-admin --region us-east-1
```
(If the stack is in `ROLLBACK_COMPLETE`, change sets cannot be created against it.)

## 7. Get back to green

- From `UPDATE_ROLLBACK_COMPLETE` (A/B): re-run `deploy.yml` with valid values (e.g. `t3.micro`, Windows root `40`, the value the stack already has). Expected final `UPDATE_COMPLETE`, or "No updates are to be performed" if values equal the current ones.
- From `ROLLBACK_COMPLETE` (C): run `destroy.yml` (or `aws cloudformation delete-stack --stack-name asrs-lab` + `wait stack-delete-complete`), then `deploy.yml` with valid values -> `CREATE_COMPLETE`.

Verify:
```bash
aws cloudformation describe-stacks --stack-name asrs-lab --query "Stacks[0].StackStatus" --profile asrs-admin --region us-east-1
aws ssm describe-instance-information --query "InstanceInformationList[].[InstanceId,PingStatus]" --profile asrs-admin --region us-east-1
DID=$(aws cloudformation detect-stack-drift --stack-name asrs-lab --query StackDriftDetectionId --output text --profile asrs-admin --region us-east-1)
sleep 30
aws cloudformation describe-stack-drift-detection-status --stack-drift-detection-id "$DID" \
  --query "[DetectionStatus,StackDriftStatus]" --profile asrs-admin --region us-east-1
```
Drift detection on the parent does not descend into nested stacks; run it per nested stack for a full check.

## 8. UPDATE_ROLLBACK_FAILED and DELETE_FAILED

**UPDATE_ROLLBACK_FAILED** (typical cause: during rollback CloudFormation needs a resource that was deleted or changed by hand). Read the failing event, fix the cause if possible (e.g. recreate the dependency, restore permissions), then:
```bash
aws cloudformation continue-update-rollback --stack-name asrs-lab --profile asrs-admin --region us-east-1
# if a resource cannot be fixed, skip it (nested format: NestedStackLogicalId.ResourceLogicalId)
aws cloudformation continue-update-rollback --stack-name asrs-lab \
  --resources-to-skip Linux.Instance --profile asrs-admin --region us-east-1
```
Skipped resources are marked rolled back but their real state may not match the template — check drift and fix afterwards. Only resources in `UPDATE_FAILED` can be skipped.

**DELETE_FAILED** (Failure D):
```bash
VPC_ID=$(aws ec2 describe-instances --instance-ids <linux-id> --query "Reservations[0].Instances[0].VpcId" --output text --profile asrs-admin --region us-east-1)
SG_X=$(aws ec2 create-security-group --group-name lab05-blocker --description "lab05 blocker" --vpc-id "$VPC_ID" \
  --query GroupId --output text --profile asrs-admin --region us-east-1)
# run destroy.yml
```
Result: Network nested stack `DELETE_FAILED` on the VPC: `The vpc 'vpc-...' has dependencies and cannot be deleted. (Service: Ec2, Status Code: 400 ... DependencyViolation)`; parent `DELETE_FAILED`. Find hand-made dependants:
```bash
aws ec2 describe-security-groups --filters Name=vpc-id,Values="$VPC_ID" --query "SecurityGroups[].[GroupId,GroupName]" --profile asrs-admin --region us-east-1
aws ec2 describe-network-interfaces --filters Name=vpc-id,Values="$VPC_ID" --query "NetworkInterfaces[].[NetworkInterfaceId,Description,Status]" --profile asrs-admin --region us-east-1
```
Preferred fix: delete the blocker and retry the delete.
```bash
aws ec2 delete-security-group --group-id "$SG_X" --profile asrs-admin --region us-east-1
aws cloudformation delete-stack --stack-name asrs-lab --profile asrs-admin --region us-east-1   # or re-run destroy.yml
aws cloudformation wait stack-delete-complete --stack-name asrs-lab --profile asrs-admin --region us-east-1
```
Last resort: `delete-stack --retain-resources <LogicalId>` (only valid while the stack is `DELETE_FAILED`, and for nested stacks applied to the nested stack itself). The retained resource is orphaned — delete it manually. Other common `DELETE_FAILED` causes: non-empty S3 bucket, IAM role with policies attached outside CloudFormation, ENIs left by Lambda/endpoints, the service role lacking delete permissions, termination protection.

Redeploy afterwards with `deploy.yml` to leave the lab running (or leave it destroyed). Snapshots from Lab 04 are unaffected by any of this.

## Common mistakes

- Reading only the parent stack events or the GitHub Actions log and reporting "Embedded stack failed" as the cause.
- Picking the *latest* FAILED event (often `Resource update cancelled`) instead of the earliest.
- Trying to re-run `deploy.yml` on a `ROLLBACK_COMPLETE` stack.
- Assuming an update failure was harmless: Failure A rebooted the instance; a valid Failure B value would have replaced it.
- Running the deploy from the admin profile with direct `create-stack` and a different role, causing permission differences vs. the pipeline.
- Using `--resources-to-skip` / `--retain-resources` and never cleaning up the orphans.
- Leaving a change set or the blocker security group behind.
