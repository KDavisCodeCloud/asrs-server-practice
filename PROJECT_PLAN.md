# Project Plan — ASRS Server Practice Lab

Goal: a repeatable AWS lab (one Amazon Linux 2023 server, one Windows Server 2022
server) that is deployed, stopped, started and destroyed through GitHub Actions,
so all server administration can be practised by hand through SSM sessions.

**Rule for the whole project: automate the infrastructure, never the exercises.**

## Phases

| # | Phase | Output | Status |
|---|---|---|---|
| 0 | Plan and directory structure | `PROJECT_PLAN.md`, folders | Done |
| 1 | CloudFormation infrastructure | `cloudformation/*.yaml` (nested stacks) | Done |
| 2 | One-time AWS bootstrap | `bootstrap/github-oidc-bootstrap.yaml` (OIDC trust, roles, artifact bucket) | Done |
| 3 | GitHub Actions | `validate.yml`, `deploy.yml`, `destroy.yml`, `stop-start.yml` | Done |
| 4 | Access and deployment docs | `docs/DEPLOYMENT.md`, `docs/ACCESS.md`, `docs/TEARDOWN.md` | Done |
| 5 | Hands-on exercises | `labs/` (25 guides) and `labs/solutions/` (25 solutions) | Done |
| 6 | Architecture, costs, interview prep | `docs/ARCHITECTURE.md`, `docs/COSTS.md`, `docs/INTERVIEW-PREP.md` | Done |
| 7 | Static validation | cfn-lint, Checkov, actionlint, `scripts/security_checks.py` pass locally | Done |
| 8 | First real deployment | Run `deploy.yml` in your AWS account, confirm both instances show **Online** in Fleet Manager | **Not started — needs your approval and AWS account** |
| 9 | Exercises | Work through `labs/` | Yours |

## Manual setup still required (cannot be automated from this repo)

1. Create a GitHub repository and push this folder to it.
2. Sign in to AWS as your IAM Identity Center administrator (see `docs/DEPLOYMENT.md` §1).
3. Deploy `bootstrap/github-oidc-bootstrap.yaml` once from your machine.
4. Create the GitHub Environments `lab` and `lab-destroy` with yourself as required reviewer.
5. Set the repository variables listed in `docs/DEPLOYMENT.md` §3.
6. Create an AWS Budget alert (`docs/COSTS.md` §4).

## Acceptance checklist

| # | Criterion | How it is met | Proven? |
|---|---|---|---|
| 1 | Deploys VPC, 2 subnets, SGs, IAM roles, 2 instances | `cloudformation/main.yaml` + nested stacks | Static validation only |
| 2 | Both servers reachable through SSM | SSM managed-instance role, egress 443, no inbound | After phase 8 |
| 3 | Manual administration from sessions | No user data, no OS configuration | By design |
| 4 | Deploys through GitHub Actions | `deploy.yml` (OIDC, environment approval) | After phase 8 |
| 5 | Stop and restart | `stop-start.yml` | After phase 8 |
| 6 | Full destroy through GitHub Actions | `destroy.yml` (typed confirmation, environment approval) | After phase 8 |
| 7 | 25 exercises documented | `labs/` | Done |
| 8 | No OS administration automated | No `UserData`, no SSM documents, no associations | Done |
| 9 | Templates pass static validation | `validate.yml`, run locally | Done |
| 10 | Nothing deploys without approval | All AWS workflows are `workflow_dispatch`; deploy/destroy gated by environments | Done |

Static validation is not a deployment. Criteria 1, 2, 4, 5 and 6 are only proven once
phase 8 has run in a real account.

## Progress log

- 2026-10-23 — Phases 0–7 built and validated locally. No AWS resources created.
