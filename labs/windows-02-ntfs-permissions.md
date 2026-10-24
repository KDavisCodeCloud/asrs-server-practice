# Windows 02 - NTFS permissions

## Objective

Design, apply, inspect and troubleshoot NTFS permissions on a folder tree, control inheritance, determine effective access for a user, and explain how share permissions combine with NTFS permissions.

## Scenario

> **Ticket REQ-2214** - Priority 3
> Finance needs a folder `C:\Data\Finance` on `asrs-lab-windows`. Members of `Finance-RW` must be able to create and edit files; `Finance-RO` may only read. Nobody else (including the general `Users` group) may see the contents. A subfolder `C:\Data\Finance\Payroll` must be readable only by `Finance-RW` - `Finance-RO` must be blocked. The folder must also be published as share `Finance` so that read-only users cannot write even if someone later loosens NTFS. Last time someone "fixed" a similar folder they wiped out all the inherited permissions; take a backup of the ACLs before changing anything.

## Prerequisites

- SSM PowerShell session on `asrs-lab-windows` (see exercise 01).
- Two test users you can sign in as, for example `fin_rw1` and `fin_ro1` (create them as part of the task). RDP via SSM port forwarding is useful for testing as those users but not required.
- Optional: Administrator password set (exercise 01) if you want to use the GUI "Effective Access" tab.

## What you need to accomplish

1. Create local groups `Finance-RW` and `Finance-RO`, and one test user in each.
2. Create `C:\Data\Finance` and `C:\Data\Finance\Payroll` with a couple of sample files.
3. Record the default ACL that the new folders inherit from `C:\` and explain where each entry comes from.
4. Back up the ACLs of `C:\Data` to a file you can restore from.
5. On `C:\Data\Finance`: stop inheritance from the parent, remove `Users` access, keep `SYSTEM` and `Administrators` full control, grant `Finance-RW` modify and `Finance-RO` read, both applying to this folder, subfolders and files.
6. On `Payroll`: ensure `Finance-RO` has no access while `Finance-RW` keeps access. Decide whether to use inheritance-break or an explicit deny and justify it.
7. Determine the effective access of each test user on both folders without guessing.
8. Create SMB share `Finance` for `C:\Data\Finance` with share permissions that cap `Finance-RO` at read.
9. Demonstrate, by testing as the users, that the most restrictive of share and NTFS applies over the share, and that only NTFS applies locally.
10. Restore from your ACL backup once (on a copy or after a deliberate mistake) to prove the backup works.

## Commands or tools to research

- `New-LocalGroup`, `Add-LocalGroupMember`, `New-LocalUser`
- `icacls` (`/save`, `/restore`, `/grant`, `/remove`, `/inheritance`, `/reset`, `/T`, inheritance flags `(OI)(CI)`, rights `F M RX R W`)
- `Get-Acl`, `Set-Acl`, `System.Security.AccessControl.FileSystemAccessRule`, `SetAccessRuleProtection`
- Folder Properties > Security > Advanced > Effective Access (GUI)
- `New-SmbShare`, `Get-SmbShare`, `Get-SmbShareAccess`, `Grant-SmbShareAccess`, `Revoke-SmbShareAccess`, `Remove-SmbShare`
- `Start-Process -Credential`, `runas`
- Sysinternals `accesschk` (optional download)

## Validation criteria

- `icacls C:\Data\Finance` shows no `(I)` entries, no `BUILTIN\Users`, and shows `Finance-RW:(OI)(CI)(M)` and `Finance-RO:(OI)(CI)(RX)`.
- As `fin_ro1`: can list and read files in `C:\Data\Finance`, gets "Access is denied" writing there, and gets "Access is denied" listing `Payroll`.
- As `fin_rw1`: can create, edit and delete files in both folders.
- `Get-SmbShareAccess Finance` shows `Finance-RO` Read, `Finance-RW` Change, no `Everyone` Full.
- Over `\\localhost\Finance`, `fin_ro1` cannot write even if you temporarily give `Finance-RO` NTFS modify.
- An `icacls /restore` from your backup returns `Successfully processed N files; Failed processing 0 files`.

## Recovery considerations

- ACL mistakes on `C:\Data` are recoverable from the `icacls /save` file or by deleting and recreating the lab folders. Never run recursive ACL commands against `C:\`, `C:\Windows` or `C:\Program Files` - breaking those can stop the SSM agent or the OS from booting. Snapshot the root volume before this lab if you are unsure.
- If you remove `Administrators`/`SYSTEM` and lock yourself out of a folder, take ownership (`takeown /F <path> /R /A`) then reset (`icacls <path> /reset /T`). Administrators always retain the right to take ownership.
- Explicit deny entries are hard to troubleshoot later and override group allows - document them if you use them.
- If ssm-user itself is affected, use Run Command (`AWS-RunPowerShellScript`, runs as SYSTEM) to fix ACLs.

## Interview angle

"A user is in a group that has Modify on the folder but still cannot save over the share - why?" - Share and NTFS permissions are evaluated separately and the most restrictive wins; also check for an inherited or explicit deny, and that the user logged off/on to refresh their token.

[Solution](solutions/windows-02-ntfs-permissions.md) — try it first.
