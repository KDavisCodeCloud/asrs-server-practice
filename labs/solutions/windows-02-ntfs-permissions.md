# Solution: Windows 02 - NTFS permissions

Run everything in the SSM PowerShell session (ssm-user, elevated) unless stated otherwise.

## 1. Groups and test users

```powershell
New-LocalGroup -Name 'Finance-RW' -Description 'Finance read/write'
New-LocalGroup -Name 'Finance-RO' -Description 'Finance read-only'

$rwPw = Read-Host -AsSecureString -Prompt 'fin_rw1 password'
$roPw = Read-Host -AsSecureString -Prompt 'fin_ro1 password'
New-LocalUser fin_rw1 -Password $rwPw -PasswordNeverExpires | Out-Null
New-LocalUser fin_ro1 -Password $roPw -PasswordNeverExpires | Out-Null
Add-LocalGroupMember 'Finance-RW' -Member fin_rw1
Add-LocalGroupMember 'Finance-RO' -Member fin_ro1

# Only so we can test with PowerShell remoting to localhost (step 7); not needed for file access
Add-LocalGroupMember 'Remote Management Users' -Member fin_rw1, fin_ro1

$rwCred = New-Object PSCredential('fin_rw1', $rwPw)
$roCred = New-Object PSCredential('fin_ro1', $roPw)
```

## 2. Folders and sample files

```powershell
New-Item -ItemType Directory -Path C:\Data\Finance\Payroll -Force | Out-Null
'Q3 budget'  | Set-Content C:\Data\Finance\budget.txt
'Salaries'   | Set-Content C:\Data\Finance\Payroll\salaries.txt
```

## 3. Inspect the inherited ACL

```powershell
icacls C:\
icacls C:\Data\Finance
```

Typical Windows Server 2022 output for the new folder:

```
C:\Data\Finance NT AUTHORITY\SYSTEM:(I)(OI)(CI)(F)
                BUILTIN\Administrators:(I)(OI)(CI)(F)
                BUILTIN\Users:(I)(OI)(CI)(RX)
                BUILTIN\Users:(I)(CI)(AD)
                BUILTIN\Users:(I)(CI)(IO)(WD)
                CREATOR OWNER:(I)(OI)(CI)(IO)(F)
```

Reading it:

| Token | Meaning |
|---|---|
| `(I)` | Inherited from a parent (here from `C:\`) |
| `(OI)` / `(CI)` | Applies to child files / child folders |
| `(IO)` | Inherit only - does not apply to this folder itself |
| `F M RX R W` | Full, Modify, Read & execute, Read, Write |
| `AD` / `WD` | Append data (create subfolders) / Write data (create files) |

So by default every local user can read the folder and create new files/subfolders in it, and becomes `CREATOR OWNER` (full control) of what they create. That is why `Users` must go.

## 4. Back up the ACLs

```powershell
New-Item -ItemType Directory C:\AclBackup -Force | Out-Null
icacls C:\Data /save C:\AclBackup\data-acl.txt /T /C
```

Expected:

```
processed file: C:\Data
processed file: C:\Data\Finance
...
Successfully processed 5 files; Failed processing 0 files
```

`/save` stores paths relative to the parent of the target, so the restore is run against `C:\` (step 10).

## 5. Lock down `C:\Data\Finance`

Disable inheritance and copy the inherited entries as explicit ones (`/inheritance:d`) so you never pass through a state with an empty ACL. Then remove `Users` and add the two groups.

```powershell
icacls C:\Data\Finance /inheritance:d
icacls C:\Data\Finance /remove:g 'BUILTIN\Users'
icacls C:\Data\Finance /grant 'Finance-RW:(OI)(CI)M' 'Finance-RO:(OI)(CI)RX'
icacls C:\Data\Finance
```

Expected:

```
C:\Data\Finance EC2AMAZ-XXXXXXX\Finance-RW:(OI)(CI)(M)
                EC2AMAZ-XXXXXXX\Finance-RO:(OI)(CI)(RX)
                NT AUTHORITY\SYSTEM:(OI)(CI)(F)
                BUILTIN\Administrators:(OI)(CI)(F)
                CREATOR OWNER:(OI)(CI)(IO)(F)
```

No `(I)` flags any more. Children (`budget.txt`, `Payroll`) still show `(I)` because they inherit from `Finance`, which is what you want.

`/inheritance:r` would remove inherited entries without copying them; if you run it before granting anything, even Administrators lose access (they can recover with `takeown`).

The same with `Get-Acl`/`Set-Acl` (for reference - do one or the other):

```powershell
$path = 'C:\Data\Finance'
$acl  = Get-Acl $path
$acl.SetAccessRuleProtection($true, $true)        # block inheritance, keep copies
Set-Acl $path $acl
$acl = Get-Acl $path                               # re-read: copies are now explicit
$acl.Access | Where-Object IdentityReference -eq 'BUILTIN\Users' |
    ForEach-Object { [void]$acl.RemoveAccessRule($_) }
$inh = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
$none = [System.Security.AccessControl.PropagationFlags]::None
$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule('Finance-RW','Modify',$inh,$none,'Allow')))
$acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule('Finance-RO','ReadAndExecute',$inh,$none,'Allow')))
Set-Acl $path $acl
```

## 6. Remove `Finance-RO` from `Payroll`

Preferred: break inheritance on `Payroll` and remove the group. Deny would also work, but deny overrides allow for anyone in both groups (for example a manager added to RO and RW), and is the usual cause of "I'm in the right group but still denied" tickets.

```powershell
icacls C:\Data\Finance\Payroll /inheritance:d
icacls C:\Data\Finance\Payroll /remove:g 'Finance-RO'
icacls C:\Data\Finance\Payroll
```

Expected: `Finance-RW`, `SYSTEM`, `Administrators`, `CREATOR OWNER`, no `Finance-RO`. Check the file inherits the change:

```powershell
icacls C:\Data\Finance\Payroll\salaries.txt
```

```
C:\Data\Finance\Payroll\salaries.txt EC2AMAZ-XXXXXXX\Finance-RW:(I)(M)
                                     NT AUTHORITY\SYSTEM:(I)(F)
                                     BUILTIN\Administrators:(I)(F)
```

The deny alternative, for comparison only: `icacls C:\Data\Finance\Payroll /deny 'Finance-RO:(OI)(CI)RX'`.

## 7. Effective access

There is no built-in PowerShell cmdlet for effective access. Three reliable methods:

**a) Test as the user (authoritative).** PowerShell remoting to localhost creates a real logon token for that user (the reason for `Remote Management Users` in step 1). WinRM is enabled by default on Server 2022.

```powershell
$test = {
    $r = @{}
    $r.ReadFinance  = try { [bool](Get-Content C:\Data\Finance\budget.txt -ErrorAction Stop) } catch { $false }
    $r.WriteFinance = try { 'x' | Set-Content C:\Data\Finance\probe.txt -ErrorAction Stop; Remove-Item C:\Data\Finance\probe.txt; $true } catch { $false }
    $r.ListPayroll  = try { [bool](Get-ChildItem C:\Data\Finance\Payroll -ErrorAction Stop) } catch { $false }
    $r.WritePayroll = try { 'x' | Set-Content C:\Data\Finance\Payroll\probe.txt -ErrorAction Stop; Remove-Item C:\Data\Finance\Payroll\probe.txt; $true } catch { $false }
    [pscustomobject]$r
}
Invoke-Command -ComputerName localhost -Credential $roCred -ScriptBlock $test
Invoke-Command -ComputerName localhost -Credential $rwCred -ScriptBlock $test
```

Expected:

```
ReadFinance WriteFinance ListPayroll WritePayroll
----------- ------------ ----------- ------------
       True        False       False        False    <- fin_ro1
       True         True        True         True    <- fin_rw1
```

If remoting returns "Access is denied", the user is not in `Remote Management Users` or WinRM is not running (`Get-Service WinRM`; `Enable-PSRemoting -Force`).

**b) GUI.** RDP in (exercise 01) > right-click `C:\Data\Finance\Payroll` > Properties > Security > Advanced > Effective Access > Select a user > `fin_ro1` > View effective access. Every row shows a red X.

**c) Sysinternals accesschk** (optional; downloads over 443): `accesschk.exe -accepteula -d fin_ro1 C:\Data\Finance\Payroll`.

## 8. Create the share

```powershell
New-SmbShare -Name Finance -Path C:\Data\Finance `
    -FullAccess 'BUILTIN\Administrators' -ChangeAccess 'Finance-RW' -ReadAccess 'Finance-RO' `
    -FolderEnumerationMode AccessBased
Get-SmbShareAccess -Name Finance
```

Expected:

```
Name    ScopeName AccountName                    AccessControlType AccessRight
----    --------- -----------                    ----------------- -----------
Finance *         BUILTIN\Administrators         Allow             Full
Finance *         EC2AMAZ-XXXXXXX\Finance-RW     Allow             Change
Finance *         EC2AMAZ-XXXXXXX\Finance-RO     Allow             Read
```

When you pass explicit access lists, `Everyone: Read` (the default) is not added. Access-based enumeration hides `Payroll` from users who cannot read it.

## 9. Share vs NTFS - most restrictive wins

Temporarily loosen NTFS so that NTFS alone would allow `Finance-RO` to write:

```powershell
icacls C:\Data\Finance /grant 'Finance-RO:(OI)(CI)M'
```

Locally (NTFS only) - `fin_ro1` can now write:

```powershell
Invoke-Command -ComputerName localhost -Credential $roCred -ScriptBlock $test   # WriteFinance = True
```

Over the share (share Read AND NTFS Modify = Read):

```powershell
net use \\localhost\Finance /user:$env:COMPUTERNAME\fin_ro1 *   # prompts for the password
'x' | Set-Content \\localhost\Finance\probe.txt
```

Expected:

```
The command completed successfully.
Set-Content : Access to the path '\\localhost\Finance\probe.txt' is denied.
```

```powershell
Get-Content \\localhost\Finance\budget.txt          # works - read is allowed by both
net use \\localhost\Finance /delete
icacls C:\Data\Finance /grant:r 'Finance-RO:(OI)(CI)RX'   # put NTFS back (/grant:r replaces)
```

Summary for the interview: local/RDP/console access = NTFS only; network access = share AND NTFS, effective right is the most restrictive of the two. Common practice is share `Authenticated Users: Change` (or Full) and do all fine-grained control in NTFS; this ticket deliberately uses the share as a second cap.

## 10. Prove the backup restores

Make a mistake, then restore:

```powershell
icacls C:\Data\Finance /grant 'Everyone:(OI)(CI)F'
icacls C:\ /restore C:\AclBackup\data-acl.txt /C
icacls C:\Data\Finance
```

The restore puts `C:\Data` and children back to the state in step 4 (inherited `Users`, no Finance groups). Re-run steps 5-6 afterwards if you want the finished configuration. Note restore target is `C:\` because the saved names are `Data`, `Data\Finance`, ...

## Cleanup / reset

```powershell
Remove-SmbShare -Name Finance -Force
Remove-Item C:\Data -Recurse -Force
Remove-Item C:\AclBackup -Recurse -Force
Remove-LocalUser fin_rw1, fin_ro1
Remove-LocalGroup 'Finance-RW', 'Finance-RO'
Get-CimInstance Win32_UserProfile | Where-Object LocalPath -match '\\fin_r[wo]1$' | Remove-CimInstance
```

## Common mistakes

- `icacls /inheritance:r` before granting anything - leaves an empty ACL; fix with `takeown /F C:\Data\Finance /R /A` then `icacls C:\Data\Finance /reset /T`.
- Granting `M` without `(OI)(CI)` - only the folder object gets the right, not files inside it.
- Forgetting `Users:(CI)(AD)`/`(WD)`: removing only the `RX` line still lets every user create files.
- Using a deny ACE where removing an allow would do.
- Testing in an existing session after changing group membership - group SIDs are in the logon token; the user must log off and on (each `Invoke-Command`/`net use` above is a new logon).
- `Set-Acl` failing with "The security identifier is not allowed to be the owner of this object" - the ACL object carries an owner your token cannot assign; use `icacls`, or `(Get-Item $path).SetAccessControl($acl)`.
- Error 1219 on `net use` - an SMB connection to that server already exists with other credentials; `net use * /delete` or use `\\127.0.0.1\Finance`.
- Restoring with `icacls C:\Data /restore ...` - wrong base directory, paths do not match.
