# Interview prep: how the lab maps to cloud operations interviews

Operations interviews (AWS cloud operations engineer, systems administrator, ASRS / server support
roles) tend to test three things:
1. Can you do routine administration without looking everything up?
2. Can you troubleshoot methodically under a vague ticket?
3. Do you understand how AWS changes the picture (IAM, SGs, SSM, EBS, CloudFormation)?

The labs follow that split.

## Exercise map

| Interview theme | Typical question | Labs |
|---|---|---|
| Identity and access on the OS | "Onboard a new admin on Linux and Windows. How do you give least privilege?" | linux-01, windows-01, linux-02, windows-02 |
| Web tier basics | "Install a web server, confirm it serves, find its logs." | linux-03, windows-03 |
| Service troubleshooting | "A service won't start after a change. Walk me through it." | linux-04, windows-04, windows-07 |
| Patching | "How do you patch a fleet safely? What about reboots and rollback?" | linux-05, windows-05 (+ Patch Manager talking points) |
| Scheduling and automation | "A nightly job didn't run. Why?" | linux-06, windows-06 |
| Performance | "Server is slow. What do you check first?" | linux-07, windows-08 |
| Storage | "Disk is full" / "grow this volume with no downtime" | linux-08, linux-09, windows-09, aws-04 |
| Networking | "Two servers can't talk. Where do you look?" | linux-10, windows-10, aws-02 |
| AWS access | "User gets AccessDenied. How do you debug it?" | aws-01 |
| Remote access without SSH/RDP | "How would you reach an instance in a private subnet?" | aws-03, docs/ACCESS.md |
| Backup and recovery | "How do you restore a single file from yesterday?" | aws-04 |
| Infrastructure as code | "Your stack rolled back. What now?" | aws-05, the repo itself |

## Answer structure for troubleshooting questions

Use the same order every time and say it out loud:

1. **Scope**: what changed, when, who is affected, one server or many?
2. **Layers, outside in**: AWS (instance status checks, SG, NACL, route, IAM) → OS (service,
   logs, resources) → application.
3. **Evidence first**: logs and metrics before changes (`journalctl`, Event Viewer, CloudWatch,
   CloudTrail).
4. **Smallest safe fix**, with a rollback: snapshot first, change one thing, verify.
5. **Prevent recurrence**: monitoring, alarm, runbook, IaC change, post-incident notes.

## Things to be ready to explain from this repo

- **Why no port 22/3389?** Session Manager uses outbound HTTPS from the agent and IAM-based auth,
  is logged in CloudTrail, and needs no bastion host or key management.
- **Why public subnets then?** Cost: they give outbound internet without a NAT Gateway. Nothing is
  exposed because the security groups have no internet ingress. In production you'd use private
  subnets with NAT or VPC endpoints. Know the endpoint names: `ssm`, `ssmmessages`, `ec2messages`.
- **Security groups vs NACLs**: SGs are stateful, allow-only and attached to ENIs, and can reference
  other SGs. NACLs are stateless, have allow and deny rules, apply at subnet level and are
  evaluated in rule-number order.
- **IMDSv2**: session-token based. It blocks SSRF-style credential theft. `HttpTokens: required`.
- **EBS**: gp3 vs gp2 (gp3 has baseline 3000 IOPS / 125 MB/s independent of size). Volumes are
  AZ-scoped, snapshots are regional and incremental. Modify Volume works online, but you then extend
  the partition and filesystem (`growpart`, `xfs_growfs`; `Resize-Partition`). You can't modify the
  same volume again for 6 hours.
- **Patching on AWS**: Systems Manager Patch Manager. Patch baselines, patch groups (the
  `PatchGroup` tag here), maintenance windows, `AWS-RunPatchBaseline` with Scan vs Install,
  compliance reporting. Fleet-level patching is automated in real life; this lab makes you do it
  by hand first so you understand what the automation does.
- **Least-privilege CI/CD**: GitHub OIDC rather than access keys. The trust policy is pinned to
  repo + environment. A separate CloudFormation service role holds the real permissions. The
  stop/start role can only touch tagged instances.
- **IaC hygiene**: change sets, drift detection, `DeletionPolicy`, why updating
  `BlockDeviceMappings` or the AMI replaces an instance, and why the AMI is pinned here.
- **Cost controls**: stop vs terminate, EBS still billed when stopped, public IPv4 charges, T3
  credits (standard vs unlimited), budgets are alerts not caps.

## Practice routine

1. Read the exercise and work it **without** the solution, under a time limit (30–45 minutes).
2. Write down your steps the way you'd write a ticket update.
3. Then read the solution and note anything you missed.
4. Reset (each solution ends with cleanup steps) and repeat a week later from memory.
5. For troubleshooting labs, set up the fault yourself, then wait a day before fixing it. You will
   have forgotten some details, which makes it closer to the real thing.

Explain each fix out loud as if to an interviewer: what you saw, what you checked, why you
changed what you changed, and how you'd stop it happening again.
