# Connecting to the servers

There is no SSH and no public RDP. Every connection goes through **AWS Systems Manager Session
Manager**, authenticated by your IAM Identity Center session and logged in CloudTrail.

## Prerequisites

```bash
aws sso login --profile asrs-admin
session-manager-plugin --version          # must be installed
export AWS_PROFILE=asrs-admin AWS_REGION=us-east-1

out() { aws cloudformation describe-stacks --stack-name asrs-lab \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text; }
LINUX=$(out LinuxInstanceId); WINDOWS=$(out WindowsInstanceId); echo "$LINUX $WINDOWS"

# Both should be Online. If not, see labs/aws-03-ssm-access.md
aws ssm describe-instance-information \
  --query "InstanceInformationList[].[InstanceId,PlatformName,PingStatus]" --output table
```

PowerShell equivalent for the ID lookup:

```powershell
$env:AWS_PROFILE='asrs-admin'; $env:AWS_REGION='us-east-1'
function out($k) { aws cloudformation describe-stacks --stack-name asrs-lab --query "Stacks[0].Outputs[?OutputKey=='$k'].OutputValue" --output text }
$LINUX = out LinuxInstanceId; $WINDOWS = out WindowsInstanceId
```

## Linux: interactive shell

```bash
aws ssm start-session --target "$LINUX"
```

You land in `sh` as **`ssm-user`**. The SSM agent creates this account on first connection and gives
it passwordless sudo through `/etc/sudoers.d/ssm-agent-users`. Useful first commands:

```bash
bash -l          # a proper bash login shell
sudo -i          # root, when an exercise needs it
cat /etc/os-release
```

Console alternative: EC2 → Instances → select → **Connect** → **Session Manager** → Connect.

Keep `ssm-user`'s sudo working. It is your way back in if you break sudo for your own users. If
you do lose root, see the recovery notes in `labs/linux-01-users-and-sudo.md`.

## Windows: PowerShell

```bash
aws ssm start-session --target "$WINDOWS"
```

You get a PowerShell session as the local account **`ssm-user`**, which the agent creates and
adds to local Administrators. Everything in the Windows labs can be done from here.

Console alternative: EC2 → **Connect** → **Session Manager**, or **Fleet Manager → Node actions →
Connect → Start terminal session**.

## Windows: desktop (RDP over Session Manager)

Port 3389 is not open in the security group, and doesn't need to be. Session Manager tunnels it:

```bash
aws ssm start-session --target "$WINDOWS" \
  --document-name AWS-StartPortForwardingSession \
  --parameters 'portNumber=3389,localPortNumber=13389'
# leave this running, then point Remote Desktop at localhost:13389
```

Fleet Manager also has a browser-based Remote Desktop: Systems Manager → Fleet Manager → select the
node → **Node actions → Connect → Connect with Remote Desktop**. It can sign in with a username and
password, or with a key pair if you deployed with one.

### Getting a Windows password for RDP

The lab deploys **without an EC2 key pair**, so the EC2 console's *Get Windows password* button
has nothing to decrypt. You need a password for a GUI sign-in. Options, safest first:

1. **Create your own local admin user** from the SSM PowerShell session. That is the task in
   `labs/windows-01-local-users-admins.md`. Sign in over RDP as that user.
2. **Reset the built-in Administrator password** from the SSM PowerShell session (the commands are
   in the lab 01 solution). Use a long random password, don't put it in shell history files or
   scripts, and store it in a password manager.
3. **AWS-native**: run the Automation runbook `AWSSupport-ResetAccess`. It resets the
   Administrator password through EC2Rescue and stores the new one, encrypted, in SSM Parameter
   Store. It stops and restarts the instance and creates temporary resources. Read the runbook's
   description before using it.
4. **Key-pair route**: create a key pair, redeploy with `KeyPairName` set (this replaces the Windows
   server), then use *Get Windows password* with the private key.

Never commit a password, `.rdp` file or private key to the repository (`.gitignore` blocks the
common file types).

## Between the two servers

Inside the VPC, only these are allowed, by security-group reference:

| From | To | Allowed |
|---|---|---|
| Linux | Windows | ICMP echo, TCP 80 |
| Windows | Linux | ICMP echo, TCP 80 |

Windows Firewall blocks inbound ping by default even though the security group allows it. Fixing
that is part of `labs/windows-10-firewall-connectivity.md`. Anything else (for example SSH from
Windows to Linux, or WinRM) needs a security-group change: `labs/aws-02-security-groups.md`.

Private IPs: stack outputs `LinuxPrivateIp` and `WindowsPrivateIp`.

## Running one-off commands (Run Command)

Run Command is available but the lab doesn't use it for you. If you want to try it:

```bash
aws ssm send-command --instance-ids "$LINUX" --document-name AWS-RunShellScript \
  --parameters 'commands=["uptime"]' --query Command.CommandId --output text
aws ssm list-command-invocations --command-id <id> --details \
  --query "CommandInvocations[].CommandPlugins[].Output" --output text
```

For Windows use `AWS-RunPowerShellScript`.

## Session logging

Session Manager doesn't store session transcripts unless you configure it (Systems Manager →
Session Manager → Preferences → S3 or CloudWatch Logs). Who started which session is recorded in
CloudTrail (`StartSession` events) either way.
