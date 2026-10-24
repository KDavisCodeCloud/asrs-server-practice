# Architecture

## Diagram

![ASRS Server Practice Lab: AWS architecture](images/asrs-architecture.png)

Detailed text version, including the identity chain:

```
                         ┌───────────────────────── AWS account (one region) ─────────────────────────┐
 You (laptop)            │                                                                            │
 aws sso login ──────────┼──► IAM Identity Center admin session                                       │
 aws ssm start-session ──┼──► Systems Manager (Session Manager) ◄──── HTTPS 443 outbound ───┐         │
                         │                                                                  │         │
 GitHub Actions ──OIDC───┼──► asrs-lab-github-deploy ──PassRole──► CloudFormation           │         │
   deploy / destroy      │                                         (asrs-lab-cfn-execution) │         │
   stop-start ───OIDC────┼──► asrs-lab-github-operator (stop/start tagged instances only)   │         │
                         │                                                                  │         │
                         │  VPC 10.20.0.0/16 ──────────────────────────────────────────────┼──────┐  │
                         │  │  Route table: 0.0.0.0/0 → Internet Gateway; S3 gateway endpoint│      │  │
                         │  │                                                                │      │  │
                         │  │  Linux subnet 10.20.1.0/24 (AZ 1)   Windows subnet 10.20.2.0/24 (AZ 2) │
                         │  │  ┌──────────────────────────┐       ┌──────────────────────────┐     │  │
                         │  │  │ asrs-lab-linux           │ ICMP  │ asrs-lab-windows         │     │  │
                         │  │  │ Amazon Linux 2023        │◄─────►│ Windows Server 2022      │     │  │
                         │  │  │ t3.micro                 │ TCP80 │ t3.small                 │     │  │
                         │  │  │ root 20 GiB gp3 (enc.)   │       │ root 40 GiB gp3 (enc.)   │     │  │
                         │  │  │ data  5 GiB gp3 raw      │       │ data  5 GiB gp3 raw      │     │  │
                         │  │  │ SG: asrs-lab-linux-sg    │       │ SG: asrs-lab-windows-sg  │     │  │
                         │  │  │ role: SSM core only      │       │ role: SSM core only      │     │  │
                         │  │  └──────────────────────────┘       └──────────────────────────┘     │  │
                         │  │  (optional) interface endpoints: ssm, ssmmessages, ec2messages     │  │
                         │  └────────────────────────────────────────────────────────────────────┘  │
                         └────────────────────────────────────────────────────────────────────────────┘
```

## Stacks and dependency order

`cloudformation/main.yaml` is the root. CloudFormation works out the order from the
`!GetAtt <Stack>.Outputs.*` references:

| Order | Nested stack | Creates | Needs |
|---|---|---|---|
| 1 | `Network` (`network.yaml`) | VPC, 2 subnets, IGW, route table, S3 gateway endpoint, optional SSM interface endpoints | nothing |
| 1 | `Iam` (`iam.yaml`) | Linux role + instance profile, Windows role + instance profile | nothing |
| 2 | `Security` (`security.yaml`) | Linux SG, Windows SG, peer rules, optional KMS key | VPC ID |
| 3 | `Linux` (`linux-ec2.yaml`) | Amazon Linux 2023 instance | subnet, SG, profile, KMS key |
| 3 | `Windows` (`windows-ec2.yaml`) | Windows Server 2022 instance | subnet, SG, profile, KMS key |
| 4 | `Storage` (`storage.yaml`, optional) | 2 data volumes + attachments | instance IDs and AZs |

`Network` and `Iam` build in parallel; so do `Linux` and `Windows`. Deletion runs in reverse.

## Design decisions

**Public subnets, no inbound rules, no NAT Gateway.** The servers need outbound HTTPS to reach
Systems Manager, package repositories and Windows Update. A NAT Gateway costs about USD 33/month
before data. Instead each server gets an auto-assigned public IPv4 for outbound traffic only, and
the security groups contain no rule that admits anything from the internet. Ports 22 and 3389 are
never open. This is a deliberate lab trade-off; in production you would normally put servers in
private subnets behind NAT or VPC endpoints (see `INTERVIEW-PREP.md`).

**Optional SSM interface endpoints.** `EnableSsmVpcEndpoints=true` adds `ssm`, `ssmmessages` and
`ec2messages` endpoints with private DNS, so SSM traffic stays inside the VPC. It is off by default
because it adds about USD 44/month if left running. It is useful for the SSM troubleshooting lab.

**S3 gateway endpoint.** Free. Amazon Linux 2023 repositories are hosted in S3, so `dnf` traffic
stays on the AWS network.

**Security groups by reference.** Linux and Windows SGs allow ICMP echo and TCP 80 only from each
other's security group, not from CIDR ranges. The cross-references are separate
`SecurityGroupIngress`/`SecurityGroupEgress` resources to avoid a circular dependency. Egress is
TCP 80/443 to anywhere plus ICMP to the peer. DNS (Route 53 Resolver) and time sync
(169.254.169.123) are link-local services that security groups do not filter.

**Instance roles.** Each server has its own role, under the IAM path `/asrs-lab/`, with only
`AmazonSSMManagedInstanceCore`. That is the minimum for Session Manager, Run Command and Fleet
Manager. Anything else (S3 access, CloudWatch agent) is left for the IAM exercises.

**Stock operating systems.** No `UserData`. The SSM agent is pre-installed on both AMIs.
`scripts/security_checks.py` fails the build if `UserData`, SSM documents, associations,
maintenance windows or patch baselines appear in a template.

**AMIs.** Resolved from AWS public SSM parameters:
`/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64` and
`/aws/service/ami-windows-latest/Windows_Server-2022-English-Full-Base`. After the first deploy,
`deploy.yml` pins the AMI that is already running. Without that, a deploy after AWS publishes a new
AMI would replace both servers and erase your work. Tick `refresh_amis` to move to the newest AMI on
purpose (it rebuilds both servers). Because the AMI is pinned, the servers fall behind on patches over
time, which gives the patching exercises something to do.

**Storage.** Root volumes are gp3 and encrypted with the AWS-managed `aws/ebs` key by default, or a
customer-managed key (`CreateKmsKey=true`) or one you supply. Each server can get a raw 5 GiB data
volume (`/dev/sdf`, shown as `/dev/nvme1n1` on Linux; `xvdf`, shown as offline Disk 1 on Windows).
Nothing formats or mounts them.

**Instance metadata.** IMDSv2 is required (`HttpTokens: required`).

**CPU credits.** `CPUCredits: standard`, so a runaway process cannot run up "unlimited mode"
surplus charges. A stuck CPU will throttle to baseline instead. You can see this happen in the
CPU exercises.

## Deployment identity chain

1. GitHub issues an OIDC token for the job. The `sub` claim names the repository and either the
   environment (`environment:lab`, `environment:lab-destroy`) or the branch (`ref:refs/heads/main`).
2. AWS STS trusts `token.actions.githubusercontent.com` and checks `aud` and `sub` in the role
   trust policy. Jobs from other repositories, branches or environments cannot assume the role.
3. `asrs-lab-github-deploy` can only call CloudFormation on stacks named `asrs-lab*`, upload to
   the artifact bucket, and pass `asrs-lab-cfn-execution` to CloudFormation.
4. CloudFormation assumes `asrs-lab-cfn-execution`, which holds the EC2, IAM (path-scoped),
   KMS and SSM-parameter permissions needed to build the lab.
5. `asrs-lab-github-operator` can only start or stop instances tagged `Project=asrs-lab`.

## Tags

Every taggable resource carries `Project=asrs-lab` and `Environment=lab`. Instances also carry
`Name`, `Server`, `OS` and `PatchGroup` (`asrs-lab-linux` / `asrs-lab-windows`). `PatchGroup`
is there so you can try Patch Manager by hand if you want to. Nothing in the stack patches.
