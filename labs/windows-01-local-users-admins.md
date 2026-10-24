# Windows 01 - Local users and administrators

## Objective

Create and manage local accounts and group membership on a standalone Windows Server 2022 instance, and set up a working RDP login path on a server that was built with no key pair and no inbound ports.

## Scenario

> **Ticket INC-3107** - Priority 3
> Two new operators start Monday. `jdoe` (senior ops) needs full administrative rights on `asrs-lab-windows`. `asmith` (helpdesk) must be able to RDP in to read logs but must NOT be a local administrator. The team also needs a known, documented local Administrator password so the server can be reached over RDP through Session Manager. Nobody has the original password: the instance was launched without a key pair.

## Prerequisites

- Stack `asrs-lab` deployed; Windows instance `asrs-lab-windows` running and showing **Online** in Systems Manager Fleet Manager.
- AWS CLI v2 and the Session Manager plugin installed on your workstation.
- Instance ID:
  ```bash
  aws ec2 describe-instances --region us-east-1 \
    --filters Name=tag:Name,Values=asrs-lab-windows Name=instance-state-name,Values=running \
    --query 'Reservations[].Instances[].InstanceId' --output text
  ```
- An RDP client (mstsc, Microsoft Remote Desktop for macOS, Remmina).
- Note: t3.small has 2 GiB RAM. The first RDP logon of any new profile takes 1-3 minutes while the profile is built. Be patient.

## What you need to accomplish

1. Open a PowerShell session on the Windows server using Session Manager only. Confirm which account you are running as and which groups it belongs to.
2. Record the current state: list all local users and the members of `Administrators` and `Remote Desktop Users`.
3. Review the local password policy so the passwords you choose will be accepted.
4. Create local user `jdoe` with a description, a password that does not expire for this lab, and make it a member of the local `Administrators` group.
5. Create local user `asmith` who can log on over RDP but has no administrative rights.
6. Set (reset) the built-in `Administrator` password to a value you control, without using the EC2 "Get Windows password" button.
7. Reach the desktop over RDP through Session Manager (port forwarding to a local port, or Fleet Manager Remote Desktop) and log on as each of `Administrator`, `jdoe` and `asmith`.
8. Prove `asmith` is not an administrator from within their own session.
9. Document where each password is stored (password manager, not the ticket).

## Commands or tools to research

- `aws ssm start-session`, document `AWS-StartPortForwardingSession`
- Fleet Manager - Remote Desktop
- `whoami /user /groups`, `net accounts`
- `Get-LocalUser`, `New-LocalUser`, `Set-LocalUser`, `Enable-LocalUser`, `Disable-LocalUser`, `Remove-LocalUser`
- `Get-LocalGroup`, `Get-LocalGroupMember`, `Add-LocalGroupMember`, `Remove-LocalGroupMember`
- `Read-Host -AsSecureString`, `ConvertTo-SecureString`
- `net user`, `net localgroup` (legacy equivalents)
- Systems Manager Run Command, document `AWS-RunPowerShellScript`

## Validation criteria

- `Get-LocalUser jdoe, asmith` shows both accounts `Enabled = True`.
- `Get-LocalGroupMember Administrators` lists `Administrator`, `ssm-user` and `jdoe` - and not `asmith`.
- `Get-LocalGroupMember 'Remote Desktop Users'` lists `asmith`.
- RDP to `localhost:13389` (or Fleet Manager) succeeds for all three accounts.
- In the `asmith` RDP session, `whoami /groups` does not contain `BUILTIN\Administrators`, and an elevated action (for example stopping a service) is denied.
- `Get-LocalUser Administrator | Select-Object PasswordLastSet` shows today's date.

## Recovery considerations

- **Do not** delete, disable or remove `ssm-user` from `Administrators`, and do not disable the `AmazonSSMAgent` service. Session Manager is your only door in.
- If you lock yourself out of the interactive session (ssm-user broken, wrong group change), Run Command (`AWS-RunPowerShellScript`) executes as `NT AUTHORITY\SYSTEM`, independent of `ssm-user`. Use it to re-enable accounts, restore group membership or reset passwords.
- If you forget a password, reset it again from an SSM session - you never need the old one.
- If the SSM agent stops reporting (Fleet Manager shows Connection Lost): wait 5 minutes, then stop/start the instance (not reboot). Last resort: `AWSSupport-ExecuteEC2Rescue` / EC2Rescue offline, or the `AWSSupport-ResetAccess` automation (needs extra IAM permissions beyond this lab's role).
- Passwords typed as plaintext on a command line can end up in PowerShell history (`(Get-PSReadLineOption).HistorySavePath`). Prefer `Read-Host -AsSecureString`.
- Taking an EBS snapshot of the root volume before account work is cheap insurance.

## Interview angle

"How do you get into a Windows EC2 instance that has no key pair and no open RDP port?" - Session Manager shell, reset the local Administrator password, then RDP through an SSM port-forwarding tunnel; Run Command as SYSTEM is the break-glass path.

[Solution](solutions/windows-01-local-users-admins.md) — try it first.
