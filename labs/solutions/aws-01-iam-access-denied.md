# Solution — AWS Lab 01: Troubleshoot IAM AccessDenied

Conventions: commands on your machine use `--profile asrs-admin --region us-east-1`. Commands inside an SSM session run as the **instance role** and must not use the profile.

## 0. Gather IDs (local)

```bash
LINUX_ID=$(aws cloudformation describe-stacks --stack-name asrs-lab \
  --query "Stacks[0].Outputs[?OutputKey=='LinuxInstanceId'].OutputValue" --output text \
  --profile asrs-admin --region us-east-1)

PROFILE_ARN=$(aws ec2 describe-instances --instance-ids "$LINUX_ID" \
  --query "Reservations[0].Instances[0].IamInstanceProfile.Arn" --output text \
  --profile asrs-admin --region us-east-1)
echo "$PROFILE_ARN"   # arn:aws:iam::<acct>:instance-profile/asrs-lab/<profile-name>

ROLE_NAME=$(aws iam get-instance-profile --instance-profile-name "${PROFILE_ARN##*/}" \
  --query "InstanceProfile.Roles[0].RoleName" --output text --profile asrs-admin)
ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text --profile asrs-admin)
ACCT=$(aws sts get-caller-identity --query Account --output text --profile asrs-admin)
BUCKET=asrs-lab-scratch-$ACCT
echo "$ROLE_NAME $ROLE_ARN $BUCKET"
```

Create the scratch bucket and a test object (skip if it exists):

```bash
aws s3api create-bucket --bucket "$BUCKET" --profile asrs-admin --region us-east-1
aws s3api put-bucket-tagging --bucket "$BUCKET" \
  --tagging 'TagSet=[{Key=Project,Value=asrs-lab}]' --profile asrs-admin --region us-east-1
echo "nightly input" > input.txt
aws s3 cp input.txt "s3://$BUCKET/input/input.txt" --profile asrs-admin --region us-east-1
```

## 1. Reproduce from the instance

```bash
aws ssm start-session --target "$LINUX_ID" --profile asrs-admin --region us-east-1
```

Inside the session (`sh-5.2$`):

```bash
aws s3 ls s3://asrs-lab-scratch-<acct>/ --region us-east-1
```
Expected:
```
An error occurred (AccessDenied) when calling the ListObjectsV2 operation: User: arn:aws:sts::<acct>:assumed-role/<role>/i-0abc... is not authorized to perform: s3:ListBucket on resource: "arn:aws:s3:::asrs-lab-scratch-<acct>" because no identity-based policy allows the s3:ListBucket action
```

```bash
aws ec2 describe-instances --region us-east-1
```
Expected: `An error occurred (UnauthorizedOperation) when calling the DescribeInstances operation: You are not authorized to perform this operation. User: arn:aws:sts::...assumed-role/<role>/i-... is not authorized to perform: ec2:DescribeInstances because no identity-based policy allows ...`

Reading the reason phrase: "no identity-based policy allows" = implicit deny (nothing grants it). "with an explicit deny in a ..." would point at an identity policy, resource policy, permissions boundary or SCP (the phrase names which). Note the S3 action is `s3:ListBucket` even though the API is `ListObjectsV2`.

## 2. Prove the principal (inside the instance)

```bash
aws sts get-caller-identity
```
```json
{ "UserId": "AROA...:i-0abc...", "Account": "<acct>",
  "Arn": "arn:aws:sts::<acct>:assumed-role/<role>/i-0abc..." }
```

Confirm the credentials come from IMDS, not a file or env var:

```bash
aws configure list          # "type" column should show iam-role
env | grep -i ^AWS_         # should be empty or region only
ls ~/.aws 2>/dev/null       # no credentials file expected

TOKEN=$(curl -sX PUT http://169.254.169.254/latest/api/token \
  -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/iam/info
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/iam/security-credentials/
```
`iam/info` returns `InstanceProfileArn`; `security-credentials/` returns the role name. A request without the token returns `401` because IMDSv2 is required — a common reason older SDKs/scripts fail to find credentials.

Exit the session (`exit`).

## 3. Inspect the role (local)

```bash
aws iam list-attached-role-policies --role-name "$ROLE_NAME" --profile asrs-admin
aws iam list-role-policies --role-name "$ROLE_NAME" --profile asrs-admin
aws iam get-role --role-name "$ROLE_NAME" --query "Role.PermissionsBoundary" --profile asrs-admin
```
Expected: one attached policy `AmazonSSMManagedInstanceCore`, `PolicyNames: []`, boundary `null`. That policy grants `ssm:*`-style agent actions, `ssmmessages:*`, `ec2messages:*` — no S3 bucket reads (only via SSM-specific paths) and no `ec2:DescribeInstances`.

Console path: IAM > Roles > search `asrs-lab` > Permissions tab.

## 4. Policy simulator (before the fix)

```bash
aws iam simulate-principal-policy --policy-source-arn "$ROLE_ARN" \
  --action-names s3:ListBucket s3:GetObject ec2:DescribeInstances \
  --resource-arns "arn:aws:s3:::$BUCKET" "arn:aws:s3:::$BUCKET/*" \
  --query "EvaluationResults[].[EvalActionName,EvalResourceName,EvalDecision]" \
  --output table --profile asrs-admin
```
All rows: `implicitDeny`. (The simulator evaluates identity policies and boundaries; it does not evaluate SCPs from the management account unless you are in an Organization context, and resource policies only if you pass them.)

## 5. CloudTrail

```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=ListObjects \
  --max-results 5 --profile asrs-admin --region us-east-1 \
  --query "Events[].CloudTrailEvent" --output text | head -c 2000
```
S3 object-level calls (`ListObjects`, `GetObject`) are **data events** and are not in Event history unless a trail logs data events. Management events are, so search the EC2 call instead:

```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=DescribeInstances \
  --max-results 10 --profile asrs-admin --region us-east-1 \
  --query "Events[].CloudTrailEvent" --output text \
  | tr '\t' '\n' | grep -i '"errorCode"' | head
```
Fields that tie it to the instance: `userIdentity.arn` (`assumed-role/<role>/i-...`), `userIdentity.sessionContext.ec2RoleDelivery`, `sourceIPAddress`, `errorCode: Client.UnauthorizedOperation`. `aws s3 ls` (no bucket) is `ListBuckets`, a management event, and does show up. Allow 5–15 minutes.

## 6. Encoded authorization message

Mutating EC2 APIs return an encoded reason. From the instance:

```bash
aws ec2 stop-instances --instance-ids i-<linux-id> --dry-run --region us-east-1
```
```
An error occurred (UnauthorizedOperation) when calling the StopInstances operation: You are not authorized to perform this operation. Encoded authorization failure message: 4Kx9...
```
Decode it locally (the instance role lacks `sts:DecodeAuthorizationMessage`):

```bash
aws sts decode-authorization-message --encoded-message '4Kx9...' \
  --query DecodedMessage --output text --profile asrs-admin | python3 -m json.tool
```
Key fields: `"allowed": false`, `"explicitDeny": false`, `"context": {"principal": ..., "action": "ec2:StopInstances", "resource": "arn:aws:ec2:...:instance/i-..."}`.

## 7. Least-privilege fix

```bash
cat > s3-read-one-bucket.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "ListOneBucket", "Effect": "Allow", "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::$BUCKET" },
    { "Sid": "ReadObjects", "Effect": "Allow", "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::$BUCKET/*" }
  ]
}
EOF
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name lab01-s3-read-scratch \
  --policy-document file://s3-read-one-bucket.json --profile asrs-admin
```
Note the split: `ListBucket` applies to the bucket ARN, `GetObject` to `bucket/*`. Putting both on one ARN is the most common mistake.

Re-run step 4: `s3:ListBucket` and `s3:GetObject` now `allowed`, `ec2:DescribeInstances` still `implicitDeny`.

From the instance (wait ~10–30 s for IAM propagation):
```bash
aws s3 ls s3://asrs-lab-scratch-<acct>/input/         # lists input.txt
aws s3 cp s3://asrs-lab-scratch-<acct>/input/input.txt -   # prints "nightly input"
aws s3 ls                                               # still AccessDenied (ListAllMyBuckets)
aws ec2 describe-instances --region us-east-1           # still UnauthorizedOperation
```
Reachability note: the Linux SG allows 443 egress and the VPC has an S3 gateway endpoint, so the network is not the issue — the error was purely IAM. A network problem would be a timeout, not AccessDenied.

## 8. Cleanup and drift check

```bash
aws iam delete-role-policy --role-name "$ROLE_NAME" --policy-name lab01-s3-read-scratch --profile asrs-admin
aws iam list-role-policies --role-name "$ROLE_NAME" --profile asrs-admin   # PolicyNames: []

# Drift: the role lives in the Iam nested stack
IAM_STACK=$(aws cloudformation describe-stack-resources --stack-name asrs-lab \
  --query "StackResources[?ResourceType=='AWS::CloudFormation::Stack' && contains(LogicalResourceId,'Iam')].PhysicalResourceId" \
  --output text --profile asrs-admin --region us-east-1)
DID=$(aws cloudformation detect-stack-drift --stack-name "$IAM_STACK" --query StackDriftDetectionId --output text --profile asrs-admin --region us-east-1)
sleep 20
aws cloudformation describe-stack-drift-detection-status --stack-drift-detection-id "$DID" \
  --query "[DetectionStatus,StackDriftStatus]" --profile asrs-admin --region us-east-1
```
Expected `["DETECTION_COMPLETE","IN_SYNC"]`. If you run drift detection while the inline policy is still attached it may report the role `MODIFIED` (depends on whether the template declares a `Policies` property). Check `describe-stack-resource-drifts` for details.

Delete the bucket if you are finished with all labs:
```bash
aws s3 rb "s3://$BUCKET" --force --profile asrs-admin --region us-east-1
rm -f input.txt s3-read-one-bucket.json
```
Re-run from step 1 to repeat.

## Common mistakes

- Using `--profile asrs-admin` inside the instance (there is no such profile there) or testing locally and concluding "it works".
- Granting `s3:*` or `AmazonS3ReadOnlyAccess` — works, but fails the least-privilege requirement.
- Single statement with `Resource: arn:aws:s3:::bucket/*` only → `ListBucket` still denied.
- Hitting IMDS without a token on an IMDSv2-required instance and assuming the role is missing.
- Searching CloudTrail Event history for `GetObject` (data event, not there) and concluding nothing was logged.
- Trying to decode the message from the instance (needs `sts:DecodeAuthorizationMessage`).
- Leaving the inline policy in place; a future stack update or audit will find unmanaged permissions.
