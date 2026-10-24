# AWS Lab 05 — Troubleshoot a CloudFormation Deployment Failure

## Objective

Cause a CloudFormation failure through the normal pipeline, find the real root cause inside the nested stack that failed, explain the resulting stack state and what operations it still allows, and return the lab to a healthy, deployable state. Also practise previewing changes safely and handling a stack that fails to delete.

## Scenario

> **Incident INC-4519 (P3):** The `deploy.yml` run for `asrs-lab` failed after approval in the `lab` environment. The workflow log only says `Waiter StackUpdateComplete failed` / the parent stack shows `Embedded stack ... was not successfully updated`. The requester changed "only the instance size". Find the actual error, explain what state the stack is in and whether production-style resources were affected, and get the pipeline green again.

## Prerequisites

- Stack `asrs-lab` deployed and healthy (`CREATE_COMPLETE` or `UPDATE_COMPLETE`).
- Access to GitHub Actions for this repo (you will approve a run in the `lab` environment).
- Admin session `--profile asrs-admin` (read stack events; CloudFormation itself acts through the service role `asrs-lab-cfn-execution`).
- `cfn-lint` installed locally (`pip install cfn-lint`).
- Choose a failure to introduce yourself (read the `workflow_dispatch` inputs in `.github/workflows/deploy.yml` to see what you can pass):
  - **Failure A (update):** run `deploy.yml` against the existing stack with Linux instance type `t3.nonexistent`.
  - **Failure B (guarded input):** run with a Windows root volume size of `8` GiB (the Windows Server 2022 AMI snapshot is 30 GiB). Note where it fails and whether the stack changed at all. Then preview, without executing, a change to a *valid* size (e.g. `45`) with a change set.
  - **Failure C (create):** after `destroy.yml`, run a fresh deploy with Failure A or B, so the failure happens during **create**.
  - **Failure D (delete, optional):** while the stack exists, create something by hand that depends on a stack resource (for example a security group in the lab VPC), then run `destroy.yml`.

## What you need to accomplish

1. Before deploying, check whether any tooling would have caught the bad value (template validation, linting, parameter constraints).
2. Run the failing deployment and record the parent stack's final status and the parent's failure message.
3. Locate the nested stack that actually failed and the first `*_FAILED` event in it; quote the resource, the status reason and the timestamp. Explain why the parent's message is not the root cause.
4. Explain the difference between the state you ended in and the other rollback states: `UPDATE_ROLLBACK_COMPLETE`, `ROLLBACK_COMPLETE`, `UPDATE_ROLLBACK_FAILED`. For each, state what you can and cannot do next.
5. For Failure B, explain why a root volume size change on an existing instance is dangerous even when the size is valid.
6. Preview the same change with a change set (including nested stacks) and identify which resources would be modified or replaced, without executing it. Delete the change set.
7. Return the stack to a healthy, updatable state and get a green `deploy.yml` run with valid values.
8. Explain how you would recover from `UPDATE_ROLLBACK_FAILED` and from `DELETE_FAILED` (do Failure D if you have time), including what can be skipped or retained.

## Commands or tools to research

- `cfn-lint`, `aws cloudformation validate-template`
- `aws cloudformation describe-stacks`, `describe-stack-events`, `describe-stack-resources`, `list-stacks --stack-status-filter`
- `aws cloudformation create-change-set` (`--include-nested-stacks`, `--use-previous-template`), `describe-change-set`, `delete-change-set`
- `aws cloudformation continue-update-rollback` (`--resources-to-skip`), `rollback-stack`
- `aws cloudformation delete-stack` (`--retain-resources`), `wait stack-delete-complete`
- `aws ec2 describe-instance-type-offerings`, `aws ec2 describe-images` (root snapshot size)
- `aws cloudformation detect-stack-drift`
- Console: CloudFormation > Stacks > (toggle "View nested") > Events; Change sets tab

## Validation criteria

- You quote the nested stack's failure reason (e.g. invalid instance type value) for Failure A, and for Failure B you can say which layer rejected the input and show the change set's `Replacement` value, not the parent's "Embedded stack ... was not successfully ..." message.
- You correctly name the end state: an update failure ends in `UPDATE_ROLLBACK_COMPLETE` (stack still usable, old resources intact); a create failure ends in `ROLLBACK_COMPLETE` (cannot be updated — must be deleted).
- A change set with valid values lists the expected resources and their `Replacement` value; it was deleted without execution.
- Final state `UPDATE_COMPLETE` (or `CREATE_COMPLETE`) from a green `deploy.yml` run; both instances Online in SSM; drift `IN_SYNC`.
- (Failure D) you identified the blocking dependency from the `DELETE_FAILED` event and completed the delete without orphaning billable resources.

## Recovery considerations

- An update failure rolls back: instances are kept, but Failure A **stops and restarts** the Linux instance during the attempted resize (brief outage, new public IP).
- A `ROLLBACK_COMPLETE` stack must be deleted (`destroy.yml`) before redeploying; this deletes all lab resources (snapshots and hand-made resources survive and keep costing).
- `continue-update-rollback --resources-to-skip` and `delete-stack --retain-resources` leave resources CloudFormation no longer tracks. Clean them up manually or they bill forever.
- Do not "fix" the failure by editing resources in the console — that adds drift on top of the failure.
- Remove anything you created for Failure D.

## Interview angle

"The parent stack only tells you a child failed. Go to the nested stack's events, sort by time, find the first FAILED event — everything after it is rollback noise. Then know your state: UPDATE_ROLLBACK_COMPLETE is fine, ROLLBACK_COMPLETE means delete and recreate, UPDATE_ROLLBACK_FAILED means continue-update-rollback."

[Solution](solutions/aws-05-cloudformation-failure.md) — try it first.
