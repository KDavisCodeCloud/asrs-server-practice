# Windows 08 - CPU and memory troubleshooting

## Objective

Find what is consuming CPU and memory on a Windows server using PowerShell counters and process data, confirm with GUI tools, capture a baseline, and relate in-guest numbers to EC2 T3 burstable behaviour and CloudWatch metrics.

## Scenario

> **Ticket INC-3302** - Priority 2
> Users report `asrs-lab-windows` "freezes for minutes at a time". The CloudWatch dashboard shows CPUUtilization stuck at about 20% even though Windows Task Manager reportedly shows 100%. Someone suggests "just add RAM". Identify the top CPU and memory consumers, tell CPU saturation apart from memory pressure, explain the 20% vs 100% discrepancy, and recommend a fix (kill, tune, resize or change credit mode) with evidence.

## Prerequisites

- Two SSM PowerShell sessions on Windows (one to generate load, one to investigate). RDP (exercise 01) for Task Manager, Resource Monitor and Performance Monitor.
- AWS CLI on your workstation with `cloudwatch:GetMetricStatistics` and `ec2:DescribeInstanceCreditSpecifications`.
- Know your instance: t3.small = 2 vCPU, 2 GiB RAM, baseline 20% per vCPU, earns 24 CPU credits/hour, maximum balance 576.
- Cost warning: T3 instances launch in **unlimited** credit mode by default. Sustained 100% CPU in unlimited mode accrues surplus-credit charges once the balance is spent. Keep load runs short (10-15 minutes) and stop them when done.

### Set up the fault (you do this yourself, in session 1)

CPU load - two busy-loop processes (one per vCPU):

```powershell
$cpuHogs = 1..2 | ForEach-Object {
    Start-Process powershell.exe -WindowStyle Hidden -PassThru `
        -ArgumentList '-NoProfile', '-Command', 'while ($true) { [math]::Sqrt(12345) | Out-Null }'
}
$cpuHogs | Select-Object Id, ProcessName
```

Memory pressure - one process that allocates and touches ~1.2 GB (passed as an encoded command, as real-world scripts often are):

```powershell
$code = @'
$blocks = New-Object System.Collections.ArrayList
1..12 | ForEach-Object {
    $b = New-Object byte[] (100MB)
    for ($i = 0; $i -lt $b.Length; $i += 4096) { $b[$i] = 1 }
    [void]$blocks.Add($b)
}
Start-Sleep -Seconds 1800
'@
$enc = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
$memHog = Start-Process powershell.exe -WindowStyle Hidden -PassThru -ArgumentList '-NoProfile', '-EncodedCommand', $enc
$memHog.Id
```

Write down the PIDs. Then pretend you do not know them and investigate from session 2.

## What you need to accomplish

1. Capture a quick health snapshot: total CPU %, processor queue length, available memory, committed memory %, pages/sec, and disk queue - sampled several times, not once.
2. Identify the top 5 processes by current CPU usage (not cumulative CPU time) and the top 5 by memory (working set and private bytes). Explain why the `CPU` property of `Get-Process` is misleading for "right now".
3. Map each suspicious process to its command line, parent process and owner.
4. Decide whether the server is CPU-bound, memory-bound, or both, and justify with the counter values and thresholds.
5. Confirm in Resource Monitor and Performance Monitor via RDP (CPU tab, Memory tab hard faults, a perfmon live chart).
6. Record a 5-minute performance baseline to a `.blg` file with a data collector set (or `logman`), and open it afterwards.
7. From the workstation, retrieve `CPUUtilization`, `CPUCreditBalance` and `CPUCreditUsage` (and `CPUSurplusCreditBalance` if in unlimited mode) for the period. Find the instance's credit specification.
8. Explain why CloudWatch and Windows may disagree on CPU %, and why CloudWatch has no memory metric for this instance by default.
9. Resolve: stop the offending processes, verify recovery, and write the recommendation.

## Commands or tools to research

- `Get-Counter` (`-Counter`, `-SampleInterval`, `-MaxSamples`, `-ListSet`), counters `\Processor(_Total)\% Processor Time`, `\System\Processor Queue Length`, `\Memory\Available MBytes`, `\Memory\% Committed Bytes In Use`, `\Memory\Pages/sec`, `\Process(*)\% Processor Time`, `\Process(*)\Private Bytes`, `\PhysicalDisk(_Total)\Current Disk Queue Length`
- `Get-Process` (`CPU`, `WorkingSet64`, `PrivateMemorySize64`, `-IncludeUserName`), `Sort-Object`, `Stop-Process`
- `Get-CimInstance Win32_Process` (`CommandLine`, `ParentProcessId`), `Win32_OperatingSystem`, `Win32_PerfFormattedData_PerfProc_Process`
- `resmon.exe`, `perfmon.exe`, `taskmgr.exe`, `logman`, `relog`, Data Collector Sets
- `aws cloudwatch get-metric-statistics`, `aws ec2 describe-instance-credit-specifications`, `aws ec2 modify-instance-credit-specification`
- CloudWatch agent (concept), T3 standard vs unlimited credits

## Validation criteria

- Your snapshot shows `% Processor Time` near 100% and processor queue length consistently > 2 per vCPU during the CPU fault, and `Available MBytes` low (< ~300) with commit % high during the memory fault.
- You name the hog PIDs and their command lines without having looked at the PIDs from session 1.
- A `.blg` file of at least 5 minutes exists and opens in `relog`/perfmon.
- You show `CPUCreditBalance` falling (or `CPUSurplusCreditBalance` rising in unlimited mode) during the load and state the credit specification.
- After cleanup, CPU returns to near idle and available memory recovers.

## Recovery considerations

- A 2 GiB instance under memory pressure pages heavily and the SSM session may become very slow or drop. Your load processes were started from session 1; `Stop-Process` from session 2 ends them, and terminating session 1 normally ends them too (verify with `Get-Process powershell`). If Windows is unresponsive, reboot or stop/start the instance from the console/CLI.
- Never `Stop-Process` a system process (`lsass`, `csrss`, `wininit`, `svchost` without checking `tasklist /svc`) to "free resources" - that can crash the server.
- If the instance was in standard mode and the credit balance hits zero, the CPU is throttled to the 20% baseline and everything (including SSM) is slow until credits rebuild. Switching to unlimited (`modify-instance-credit-specification`) restores performance at extra cost.
- Delete data collector sets and large `.blg` files when done; on a 40 GiB root volume, a forgotten collector fills the disk.

## Interview angle

"Windows says 100% CPU, CloudWatch says 20% - which is right?" - Both: on a T3 out of credits the hypervisor caps the instance at its baseline, so the guest sees 100% of what it is allowed while CloudWatch reports usage relative to the full vCPU. Check `CPUCreditBalance`; fix with unlimited mode, a right-sized non-burstable type (m6i/m7i) or by fixing the workload. Memory needs the CloudWatch agent.

[Solution](solutions/windows-08-cpu-memory.md) — try it first.
