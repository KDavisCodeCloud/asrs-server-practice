# Solution: Windows 01 - Local users and administrators

All commands run in the Windows PowerShell 5.1 session that Session Manager opens, unless marked "workstation" or "Linux".

## 1. Open a session and check your identity

Workstation:

```bash
WIN_ID=$(aws ec2 describe-instances --region us-east-1 \
  --filters Name=tag:Name,Values=asrs-lab-windows Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)
aws ssm start-session --region us-east-1 --target "$WIN_ID"
```

Windows:

```powershell
whoami
whoami /groups | Select-String 'Administrators|Mandatory Label'
```

Expected:

```
ec2amaz-xxxxxxx\ssm-user
BUILTIN\Administrators   Alias   S-1-5-32-544   Mandatory group, Enabled by default, Enabled group, Group owner
Mandatory Label\High Mandatory Level   Label   S-1-16-12288
```

`ssm-user` is a local administrator and the session token is already elevated (High Mandatory Level), so no UAC prompt is involved.

## 2. Record the current state

```powershell
Get-LocalUser | Format-Table Name, Enabled, PasswordLastSet, LastLogon -AutoSize
Get-LocalGroupMember Administrators
Get-LocalGroupMember 'Remote Desktop Users'
```

Typical output on a fresh AMI:

```
Name               Enabled PasswordLastSet       LastLogon
----               ------- ---------------       ---------
Administrator      True    10/23/2026 1:02:11 PM
DefaultAccount     False
Guest              False
ssm-user           True    10/23/2026 1:20:44 PM
WDAGUtilityAccount False

ObjectClass Name                         PrincipalSource
----------- ----                         ---------------
User        EC2AMAZ-XXXXXXX\Administrator Local
User        EC2AMAZ-XXXXXXX\ssm-user      Local
```

`Remote Desktop Users` is empty.

## 3. Check password policy

```powershell
net accounts
secedit /export /cfg $env:TEMP\secpol.cfg /areas SECURITYPOLICY | Out-Null
Select-String -Path $env:TEMP\secpol.cfg -Pattern 'PasswordComplexity|MinimumPasswordLength'
```

`PasswordComplexity = 1` means the password must contain three of: upper case, lower case, digit, symbol, and must not contain the account name. Use at least 14 characters.

## 4. Create `jdoe` as an administrator

```powershell
$pw = Read-Host -AsSecureString -Prompt 'Password for jdoe'
New-LocalUser -Name 'jdoe' -Password $pw -FullName 'Jane Doe' `
    -Description 'Senior ops - INC-3107' -PasswordNeverExpires
Add-LocalGroupMember -Group 'Administrators' -Member 'jdoe'
```

Expected:

```
Name Enabled Description
---- ------- -----------
jdoe True    Senior ops - INC-3107
```

`Add-LocalGroupMember` returns nothing on success. Administrators can already log on over RDP (the "Allow log on through Remote Desktop Services" right includes `Administrators`), so `jdoe` does not need `Remote Desktop Users`.

If `Read-Host -AsSecureString` misbehaves in your terminal, the fallback is:

```powershell
$pw = ConvertTo-SecureString 'Lab-Passw0rd-2026!' -AsPlainText -Force
```

That puts the password in history if PSReadLine is loaded; clear it afterwards (see cleanup).

## 5. Create `asmith` as an RDP-only user

```powershell
$pw = Read-Host -AsSecureString -Prompt 'Password for asmith'
New-LocalUser -Name 'asmith' -Password $pw -FullName 'Alex Smith' `
    -Description 'Helpdesk - RDP only - INC-3107' -PasswordNeverExpires
Add-LocalGroupMember -Group 'Remote Desktop Users' -Member 'asmith'
```

`New-LocalUser` adds the account to `Users` automatically. In production you would leave password expiry on and set `-UserMayNotChangePassword:$false`; `-PasswordNeverExpires` here keeps the lab repeatable.

## 6. Set the built-in Administrator password

The EC2 "Get Windows password" feature decrypts a password that EC2Launch encrypted with your key pair at first boot. No key pair was used, so there is nothing to decrypt; you simply set a new one as an administrator.

```powershell
$pw = Read-Host -AsSecureString -Prompt 'New Administrator password'
Set-LocalUser -Name 'Administrator' -Password $pw
Get-LocalUser Administrator | Select-Object Name, Enabled, PasswordLastSet
```

Legacy equivalent (prompts twice, nothing on the command line):

```powershell
net user Administrator *
```

If the account were disabled: `Enable-LocalUser Administrator`.

## 7. RDP through Session Manager

Option A - port forwarding (workstation, leave this running in its own terminal):

```bash
aws ssm start-session --region us-east-1 --target "$WIN_ID" \
  --document-name AWS-StartPortForwardingSession \
  --parameters 'portNumber=3389,localPortNumber=13389'
```

Expected:

```
Starting session with SessionId: you-0abc123...
Port 13389 opened for sessionId you-0abc123...
Waiting for connections...
```

Then RDP to `localhost:13389`. Username formats that work: `Administrator`, `.\jdoe`, `.\asmith` (the `.\` forces a local account). Accept the self-signed certificate warning.

The tunnel terminates at the SSM agent on the instance, which connects to `localhost:3389`. No security group rule or Windows Firewall rule for 3389 is needed because the traffic never arrives on the network interface.

Option B - Console: Systems Manager > Fleet Manager > select the instance > Node actions > Connect > Connect with Remote Desktop > "User credentials" > enter the username and password.

## 8. Prove `asmith` is not an admin

Inside the `asmith` RDP session, open PowerShell:

```powershell
whoami /groups | Select-String 'Administrators'      # no output
Stop-Service Spooler
```

Expected:

```
Stop-Service : Service 'Print Spooler (Spooler)' cannot be stopped due to the following error: Cannot open Spooler service on computer '.'.
```

From the ssm-user session you can also confirm:

```powershell
Get-LocalGroupMember Administrators | Where-Object Name -like '*asmith'   # no output
```

## 9. Validation (one block)

```powershell
Get-LocalUser jdoe, asmith | Format-Table Name, Enabled, PasswordExpires, Description
Get-LocalGroupMember Administrators | Select-Object -ExpandProperty Name
Get-LocalGroupMember 'Remote Desktop Users' | Select-Object -ExpandProperty Name
quser    # shows active RDP sessions while you are logged in
```

Security log check that the RDP logons happened (logon type 10 = RemoteInteractive):

```powershell
Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4624; StartTime=(Get-Date).AddHours(-1)} |
  Where-Object { $_.Properties[8].Value -eq 10 } |
  ForEach-Object { '{0}  {1}' -f $_.TimeCreated, $_.Properties[5].Value }
```

## Break-glass: Run Command as SYSTEM

If the ssm-user session stops working after a mistake, from the workstation:

```bash
aws ssm send-command --region us-east-1 --instance-ids "$WIN_ID" \
  --document-name AWS-RunPowerShellScript \
  --parameters 'commands=["Enable-LocalUser ssm-user","Add-LocalGroupMember -Group Administrators -Member ssm-user -ErrorAction SilentlyContinue","whoami"]' \
  --query 'Command.CommandId' --output text
aws ssm get-command-invocation --region us-east-1 --instance-id "$WIN_ID" --command-id <id> \
  --query StandardOutputContent --output text
```

Output shows `nt authority\system`.

## Cleanup / reset

```powershell
# Sign out any RDP sessions first (quser, then logoff <id>)
Remove-LocalUser -Name jdoe, asmith
Get-CimInstance Win32_UserProfile |
  Where-Object { $_.LocalPath -match '\\(jdoe|asmith)$' } |
  Remove-CimInstance
# Clear history if you typed a plaintext password
Remove-Item (Get-PSReadLineOption).HistorySavePath -ErrorAction SilentlyContinue
```

`Remove-LocalUser` does not delete the profile folder or its registry entry; removing the `Win32_UserProfile` instance does both. Keep the Administrator password you set (later exercises use RDP).

## Common mistakes

- Typing `jdoe` at the RDP prompt and having the client send a domain/Microsoft account; use `.\jdoe`.
- Adding `asmith` to `Administrators` "just to get RDP working" - the correct group is `Remote Desktop Users`.
- Using `sc`/`net` muscle memory wrong: `net user Administrator NewPass` puts the password on the command line; `net user Administrator *` prompts.
- Password rejected with "The password does not meet the password policy requirements" - it contains the username or fails complexity.
- Trying to RDP to the instance's public IP - there is no inbound 3389 by design.
- Closing the port-forwarding terminal and wondering why RDP disconnects; the tunnel lives only as long as that `start-session` process.
- Removing `ssm-user` from Administrators to "tidy up" - recover with Run Command as above.
