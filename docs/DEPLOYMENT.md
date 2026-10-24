# Deployment

Everything here is manual and needs your approval. Nothing deploys on push.

## 0. Tools on your machine

- AWS CLI v2: `aws --version`
- Session Manager plugin: `session-manager-plugin --version`
  (install: https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
- Git, and optionally the GitHub CLI `gh`

## 1. Your AWS administrator identity (IAM Identity Center)

Use IAM Identity Center for yourself instead of an IAM user with access keys. You get short-lived
credentials from a browser sign-in, and there are no permanent keys to leak.

One-time setup, as the account root user or an existing administrator:

1. Turn on MFA for the root user. After this setup, stop using root for daily work.
2. Console → **IAM Identity Center** → **Enable**. Pick your home region and keep the built-in
   Identity Center directory.
3. **Users → Add user**: your name and email. Accept the invitation email, set a password and
   register an MFA device.
4. **Permission sets → Create** → predefined `AdministratorAccess`. Set the session duration to
   4–8 hours.
5. **AWS accounts** → select your account → **Assign users or groups** → your user → the
   `AdministratorAccess` permission set.
6. Note the **AWS access portal URL** on the Identity Center dashboard (looks like
   `https://d-xxxxxxxxxx.awsapps.com/start`).

On your machine:

```bash
aws configure sso --profile asrs-admin
#   SSO session name:       asrs
#   SSO start URL:          <your access portal URL>
#   SSO region:             <Identity Center home region>
#   SSO registration scopes: sso:account:access
#   then choose the account and the AdministratorAccess role, default region e.g. us-east-1

aws sso login --profile asrs-admin
aws sts get-caller-identity --profile asrs-admin   # should show .../AWSReservedSSO_AdministratorAccess_.../you
```

`aws sso login` again whenever the session expires. Never run `aws configure` with access keys
for this lab.

## 2. Repository and one-time bootstrap

### 2.1 Create the GitHub repository

```bash
cd "ASRS Server practice"
git add . && git commit -m "ASRS server practice lab"
gh repo create asrs-server-practice --public --source . --push   # public: Environment approvals work on the free plan
# or create the repo in the GitHub UI and: git remote add origin ... && git push -u origin main
```

The default branch must be `main`, or change `DefaultBranch` in the bootstrap.

### 2.2 Deploy the bootstrap stack

The bootstrap creates:
- the GitHub OIDC identity provider;
- an S3 bucket for packaged templates;
- the CloudFormation execution role `asrs-lab-cfn-execution`;
- the GitHub roles `asrs-lab-github-deploy` and `asrs-lab-github-operator`.

Deploy it once, from your machine:

```bash
# Does the account already have the GitHub OIDC provider? (only one is allowed)
aws iam list-open-id-connect-providers --profile asrs-admin
#   if one ends in token.actions.githubusercontent.com, use CreateOidcProvider=false below

aws cloudformation deploy \
  --profile asrs-admin --region us-east-1 \
  --stack-name asrs-lab-bootstrap \
  --template-file bootstrap/github-oidc-bootstrap.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides GitHubOrg=<your-github-user> GitHubRepo=asrs-server-practice CreateOidcProvider=true

aws cloudformation describe-stacks --profile asrs-admin --region us-east-1 \
  --stack-name asrs-lab-bootstrap --query "Stacks[0].Outputs" --output table
```

`GitHubOrg` and `GitHubRepo` are case-sensitive and must match the repository exactly. If they
are wrong, the workflows fail with `Not authorized to perform sts:AssumeRoleWithWebIdentity`.

The bootstrap is free apart from a few kilobytes in S3. Leave it in place between lab sessions.

## 3. GitHub Environments and variables

Repository → **Settings → Environments**:

| Environment | Required reviewers | Deployment branches | Used by |
|---|---|---|---|
| `lab` | you | `main` only (Selected branches) | `deploy.yml` |
| `lab-destroy` | you | `main` only | `destroy.yml` |

Required reviewers on private repositories need a paid GitHub plan (Pro, Team or Enterprise). On a
free plan, either make the repository public (the templates contain no secrets or account IDs) or
accept that the gates are the typed confirmation and the manual trigger. The OIDC trust still
limits AWS access to jobs that run in these environments.

Repository → **Settings → Secrets and variables → Actions → Variables** (variables, not secrets.
None of these are sensitive):

| Variable | Value (from the bootstrap outputs) |
|---|---|
| `AWS_REGION` | `AwsRegion`, e.g. `us-east-1` |
| `AWS_DEPLOY_ROLE_ARN` | `DeployRoleArn` |
| `AWS_OPERATOR_ROLE_ARN` | `OperatorRoleArn` |
| `CFN_EXECUTION_ROLE_ARN` | `CfnExecutionRoleArn` |
| `ARTIFACT_BUCKET` | `ArtifactBucketName` |
| `STACK_NAME` | optional, default `asrs-lab` |

With the CLI:

```bash
gh variable set AWS_REGION --body us-east-1
gh variable set AWS_DEPLOY_ROLE_ARN --body arn:aws:iam::<acct>:role/asrs-lab-github-deploy
# ...and so on
```

## 4. Deploy the lab

Actions → **deploy** → **Run workflow**. The defaults are right for a first run:

| Input | Default | Notes |
|---|---|---|
| `linux_instance_type` | `t3.micro` | |
| `windows_instance_type` | `t3.small` | `t3.medium` (4 GiB) makes the Windows desktop and Windows Update much faster, at double the cost |
| `linux_root_gib` / `windows_root_gib` | 20 / 40 | Volumes can be enlarged later but never shrunk |
| `create_data_volumes` | true | Raw 5 GiB volume on each server for disk exercises |
| `create_kms_key` | false | true creates a customer-managed key (about USD 1/month) |
| `enable_ssm_vpc_endpoints` | false | About USD 0.06/hour. Only for SSM endpoint practice |
| `availability_zones` | blank | `us-east-1a,us-east-1b` style. Set it if your first AZ has no t3 capacity |
| `refresh_amis` | false | true rebuilds both servers on the newest AMIs |

Approve the run when GitHub asks (Environment `lab`). The job:

1. Runs cfn-lint and the lab guardrails.
2. Assumes `asrs-lab-github-deploy` through OIDC.
3. Refuses to continue if the stack is stuck (`ROLLBACK_COMPLETE`, `*_IN_PROGRESS` and similar).
4. Pins the running AMIs (unless `refresh_amis`).
5. Packages the nested templates to S3 and runs `aws cloudformation deploy`, which waits for the
   stack to finish creating or updating.
6. Writes the VPC, subnet, instance and data-volume IDs, private IPs and AMI IDs to the run summary.
7. Waits up to 20 minutes for both servers to show **Online** in Systems Manager, then prints the
   connection commands.

The first deploy takes about 5–8 minutes for the stack. Windows needs another 5–10 minutes before
it registers with SSM.

If it fails, the summary lists the failed resources from the root and nested stacks. See
`labs/aws-05-cloudformation-failure.md` for how to read them.

## 5. Updating

Run **deploy** again with different inputs. CloudFormation changes only what differs. Some
changes **replace the servers** and erase everything you did on them:

- instance type: **no**, CloudFormation stops and restarts the server (brief downtime);
- data volume size: **no**, modified in place;
- root volume size (`linux_root_gib`, `windows_root_gib`): **yes, replaces that server**.
  CloudFormation replaces an instance when anything in its block device mapping changes. To
  practise growing a root disk, enlarge it with EC2 Modify Volume instead
  (`labs/linux-09`, `labs/windows-09`), and keep deploying with the original size;
- `refresh_amis` or `availability_zones`: **yes, replaces both servers**;
- turning `create_kms_key` on or off: **yes, replaces both servers** (the root volumes' key changes).

Before any deploy that changes inputs, you can preview it: CloudFormation console → stack →
**Stack actions → Create change set**. Look for `Replacement: True`.

Changes you make by hand in the console (security group rules, role policies) are drift.
`aws cloudformation detect-stack-drift --stack-name asrs-lab` shows them. A redeploy with an
unchanged template does not revert them.

## 6. Day-to-day

- Start of session: **stop-start → start**.
- End of session: **stop-start → stop**. Disks are kept, compute charges stop.
- Finished with the lab for a while: **destroy** (`docs/TEARDOWN.md`).
