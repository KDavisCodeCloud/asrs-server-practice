# AWS Lab 01 — Troubleshoot IAM AccessDenied

## Objective

Diagnose an `AccessDenied` / `UnauthorizedOperation` error raised by an EC2 instance role, prove which identity and policy are involved, and fix it with the smallest possible permission. Then remove the fix and return the role to its template-defined state.

## Scenario

> **Ticket INC-4471 (P3):** An application team reports that a nightly job on `asrs-lab-linux` cannot read its input files from S3. The job log shows `An error occurred (AccessDenied) when calling the ListObjectsV2 operation`. They also tried to list EC2 instances from the box "to check it is the right server" and got `UnauthorizedOperation`. They have asked for "admin on the role so it just works". Find the cause, grant only what the job needs (read-only access to one bucket), and document how you proved it.

## Prerequisites

- Lab stack `asrs-lab` deployed and `asrs-lab-linux` online in Session Manager.
- Local AWS CLI v2 + Session Manager plugin; admin session: `aws sso login --profile asrs-admin`.
- A scratch S3 bucket you own in `us-east-1` (create one, e.g. `asrs-lab-scratch-<account-id>`) containing one small test object. Tag it `Project=asrs-lab`.
- Known baseline: the instance role (path `/asrs-lab/`) has **only** `AmazonSSMManagedInstanceCore`. IMDSv2 is required on the instance.

## What you need to accomplish

1. Reproduce both errors from inside `asrs-lab-linux` (one S3 call, one EC2 read call) and capture the exact error text.
2. Prove which principal the instance is using: the role name, the assumed-role ARN, and the instance profile it comes from. Confirm the credentials come from instance metadata, not from a stray credentials file or environment variable.
3. From your admin session, list every policy attached to that role (managed and inline) and explain from the error message why the call is denied (no allow vs. explicit deny vs. resource policy vs. SCP / permissions boundary).
4. Use the IAM policy simulator to show, before changing anything, that the role is denied `s3:ListBucket` and `s3:GetObject` on your bucket.
5. Find the denied call in CloudTrail and identify the fields that prove it was this instance (user identity, source IP, error code).
6. Trigger an EC2 error that returns an **encoded** authorization message and decode it.
7. Grant least-privilege access: an inline policy on the role that allows listing and reading **only** your scratch bucket. Re-run the simulator and the original command from the instance; both should succeed. Confirm other buckets and `ec2:DescribeInstances` are still denied.
8. Remove the inline policy, confirm the original error returns, and check the stack for drift.

## Commands or tools to research

- IMDSv2 token flow (`PUT /latest/api/token`, `/latest/meta-data/iam/`)
- `aws sts get-caller-identity`, `aws configure list`
- `aws ec2 describe-instances` (IamInstanceProfile), `aws iam get-instance-profile`
- `aws iam list-attached-role-policies`, `aws iam list-role-policies`, `aws iam get-role`
- `aws iam simulate-principal-policy`
- `aws cloudtrail lookup-events`
- `aws sts decode-authorization-message` and `--dry-run` on EC2 calls
- `aws iam put-role-policy`, `aws iam delete-role-policy`
- `aws cloudformation detect-stack-drift`, `describe-stack-resource-drifts`
- Console: IAM > Roles; IAM Policy Simulator; CloudTrail > Event history

## Validation criteria

- You can state the assumed-role ARN (`arn:aws:sts::<acct>:assumed-role/<role>/i-...`) and the reason phrase in the error (e.g. "no identity-based policy allows").
- Simulator shows `implicitDeny` before the fix and `allowed` after, for your bucket only.
- CloudTrail event with `errorCode` `AccessDenied` found for the role session.
- Encoded message decoded to readable JSON showing the denied action and resource.
- After the fix: `aws s3 ls s3://<your-bucket>` works from the instance; `aws s3 ls` (all buckets) and `aws ec2 describe-instances` still fail.
- After cleanup: role has only `AmazonSSMManagedInstanceCore` again; drift detection reports `IN_SYNC` for the role.

## Recovery considerations

- An inline policy added by hand is **out-of-band**. It may or may not appear as drift, and a later stack update of that role can silently remove it. Never leave a hand-made fix in place; put real changes in `cloudformation/` and deploy via `deploy.yml`.
- Do **not** detach `AmazonSSMManagedInstanceCore` while working here — you will lose your shell (that is Lab 03).
- Credentials on the instance are cached for up to ~1 hour, but new role permissions apply on the next API call; IAM itself is eventually consistent (allow a few seconds to a minute).
- CloudTrail Event history can lag 5–15 minutes.
- Cost of leftovers: the scratch bucket is cents, but delete it when done (`destroy.yml` does not know about it).

## Interview angle

"Walk me through an AccessDenied": identify the principal first (`get-caller-identity`), read the reason phrase, check identity policy, resource policy, boundary and SCP in that order, prove with the simulator and CloudTrail, fix with least privilege scoped by ARN.

[Solution](solutions/aws-01-iam-access-denied.md) — try it first.
