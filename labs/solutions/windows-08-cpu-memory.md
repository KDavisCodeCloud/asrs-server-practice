# Solution: Windows 08 - CPU and memory troubleshooting

Investigate from SSM session 2 while the load from session 1 is running.

## 1. Health snapshot (sampled)

```powershell
$counters = @(
    '\Processor(_Total)\% Processor Time'
    '\System\Processor Queue Length'
    '\Memory\Available MBytes'
    '\Memory\% Committed Bytes In Use'
    '\Memory\Pages/sec'
    '\PhysicalDisk(_Total)\Current Disk Queue Length'
)
Get-Counter -Counter $counters -SampleInterval 2 -MaxSamples 5 |
    ForEach-Object {
        $_.CounterSamples | ForEach-Object {
            [pscustomobject]@{ Time = $_.Timestamp.ToString('HH:mm:ss'); Counter = ($_.Path -split '\\')[-1]; Value = [math]::Round($_.CookedValue, 1) }
        }
    } | Format-Table -AutoSize
```

Typical during CPU + memory faults:

```
Time     Counter                        Value
----     -------                        -----
16:40:02 % processor time               99.6
16:40:02 processor queue length           9
16:40:02 available mbytes               142
16:40:02 % committed bytes in use        91.3
16:40:02 pages/sec                     1834.0
16:40:02 current disk queue length        3
```

Totals from the OS:

```powershell
$os = Get-CimInstance Win32_OperatingSystem
[pscustomobject]@{
    RAM_MB        = [math]::Round($os.TotalVisibleMemorySize / 1KB)
    FreeRAM_MB    = [math]::Round($os.FreePhysicalMemory / 1KB)
    CommitLimitMB = [math]::Round($os.TotalVirtualMemorySize / 1KB)
    FreeCommitMB  = [math]::Round($os.FreeVirtualMemory / 1KB)
    vCPU          = $env:NUMBER_OF_PROCESSORS
}
```

## 2. Top consumers

`Get-Process` `CPU` is total processor seconds since the process started, so a service that has been running for a week tops the list even when idle. Measure a delta instead:

```powershell
$first = Get-Process | Select-Object Id, ProcessName, CPU
Start-Sleep -Seconds 5
$second = Get-Process | Select-Object Id, ProcessName, CPU
$cores = [int]$env:NUMBER_OF_PROCESSORS
$second | ForEach-Object {
    $prev = $first | Where-Object Id -eq $_.Id
    if ($prev -and $_.CPU) {
        [pscustomobject]@{ Id = $_.Id; Name = $_.ProcessName
            'CPU%' = [math]::Round((($_.CPU - $prev.CPU) / 5) / $cores * 100, 1) }
    }
} | Sort-Object 'CPU%' -Descending | Select-Object -First 5
```

```
  Id Name       CPU%
  -- ----       ----
4120 powershell 49.2
4388 powershell 48.7
 912 svchost     0.6
...
```

(Percent of the whole machine: two hogs at ~50% each = both vCPUs busy.)

Counter-based alternative (WMI calculates between two calls, so run it twice if the first returns zeros):

```powershell
Get-CimInstance Win32_PerfFormattedData_PerfProc_Process |
    Where-Object Name -notin '_Total', 'Idle' |
    Sort-Object PercentProcessorTime -Descending | Select-Object -First 5 Name, IDProcess,
        @{n='CPU%';e={ [math]::Round($_.PercentProcessorTime / $env:NUMBER_OF_PROCESSORS, 1) }}
```

Memory:

```powershell
Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 5 Id, ProcessName,
    @{n='WorkingSetMB';e={ [math]::Round($_.WorkingSet64 / 1MB) }},
    @{n='PrivateMB';e={ [math]::Round($_.PrivateMemorySize64 / 1MB) }}
```

```
  Id ProcessName WorkingSetMB PrivateMB
  -- ----------- ------------ ---------
4602 powershell           905      1268
1840 MsMpEng              160       230
...
```

Working set = pages currently in RAM; private bytes = memory committed to that process alone (what a leak grows). Working set below private bytes means part of it has been paged out - a sign of pressure.

## 3. Who are they?

```powershell
foreach ($id in 4120, 4388, 4602) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$id"
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)"
    [pscustomobject]@{
        Id          = $id
        Owner       = (Invoke-CimMethod -InputObject $p -MethodName GetOwner).User
        Parent      = "$($parent.Name) ($($p.ParentProcessId))"
        Started     = $p.CreationDate
        CommandLine = $p.CommandLine
    }
} | Format-List
```

```
Id          : 4602
Owner       : ssm-user
Parent      : powershell.exe (3980)
CommandLine : "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -EncodedCommand JABiAGwAbwBjAGsA...
```

Decode an encoded command (a classic thing to check on a suspicious process):

```powershell
$b64 = ((Get-CimInstance Win32_Process -Filter 'ProcessId=4602').CommandLine -split '-EncodedCommand ')[1]
[Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($b64.Trim()))
```

`Get-Process -IncludeUserName` also shows the owner (requires elevation, which ssm-user has).

## 4. Diagnosis

| Signal | Rule of thumb | Observed | Meaning |
|---|---|---|---|
| `% Processor Time` | > 85-90% sustained | ~100% | CPU saturated |
| `Processor Queue Length` | > 2 per vCPU sustained | 6-10 on 2 vCPU | Threads waiting for CPU |
| `Available MBytes` | < 10% of RAM / < ~200-300 MB | ~140 | Physical memory pressure |
| `% Committed Bytes In Use` | > 90% | ~91 | Near commit limit (RAM + page file) - risk of out-of-memory errors |
| `Pages/sec` | high and sustained together with low available memory | ~1800 | Hard faults to the page file, disk becomes the bottleneck |

Both: CPU-bound (two busy processes) and memory-bound (one 1.2 GB process on a 2 GiB box). "Just add RAM" would not fix the CPU half.

## 5. GUI confirmation (RDP)

- **Task Manager** > Details: add columns "CPU", "Memory (private working set)", "Command line".
- **Resource Monitor** (`resmon`) > CPU tab: sort by "Average CPU"; Memory tab: "Hard Faults/sec" per process and the physical memory bar (In Use / Modified / Standby / Free).
- **Performance Monitor** (`perfmon`) > Monitoring Tools > Performance Monitor: add `Processor(_Total)\% Processor Time`, `Memory\Available MBytes`, `Memory\Pages/sec`; switch to Report view for exact values.

Expect the GUI to be sluggish while the load runs.

## 6. Baseline with logman

```powershell
New-Item -ItemType Directory C:\PerfLogs -Force | Out-Null
logman create counter INC3302 -o C:\PerfLogs\INC3302 -f bincirc -max 100 -si 00:00:05 -rf 00:05:00 -c `
    "\Processor(_Total)\% Processor Time" "\System\Processor Queue Length" `
    "\Memory\Available MBytes" "\Memory\% Committed Bytes In Use" "\Memory\Pages/sec" `
    "\Process(*)\% Processor Time" "\Process(*)\Private Bytes" "\PhysicalDisk(_Total)\Current Disk Queue Length"
logman start INC3302
logman query INC3302          # Status: Running; stops by itself after 5 minutes (-rf)
```

After 5 minutes:

```powershell
$blg = Get-ChildItem C:\PerfLogs\INC3302*.blg | Select-Object -First 1 -ExpandProperty FullName
relog $blg -q                              # time range, sample count
Import-Counter -Path $blg -Counter '\\*\Processor(_Total)\% Processor Time', '\\*\Memory\Available MBytes' |
    ForEach-Object { $_.CounterSamples } |
    Group-Object { ($_.Path -split '\\')[-1] } |
    ForEach-Object { $m = $_.Group.CookedValue | Measure-Object -Average -Minimum -Maximum
        [pscustomobject]@{ Counter = $_.Name; Avg = [math]::Round($m.Average,1); Min = [math]::Round($m.Minimum,1); Max = [math]::Round($m.Maximum,1) } }
relog $blg -f csv -o C:\PerfLogs\INC3302.csv
```

Open the `.blg` in perfmon (View Log Data) over RDP for graphs. A GUI-created Data Collector Set (perfmon > Data Collector Sets > User Defined) produces the same file.

## 7. CloudWatch and credit mode (workstation)

```bash
aws ec2 describe-instance-credit-specifications --instance-ids "$WIN_ID" \
  --query 'InstanceCreditSpecifications[0].CpuCredits' --output text        # unlimited (T3 default) or standard

START=$(date -u -d '-2 hours' +%Y-%m-%dT%H:%M:%SZ)     # macOS: date -u -v-2H +%Y-%m-%dT%H:%M:%SZ
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
for M in CPUUtilization CPUCreditBalance CPUCreditUsage CPUSurplusCreditBalance; do
  echo "== $M"
  aws cloudwatch get-metric-statistics --namespace AWS/EC2 --metric-name "$M" \
    --dimensions Name=InstanceId,Value="$WIN_ID" --start-time "$START" --end-time "$END" \
    --period 300 --statistics Average \
    --query 'sort_by(Datapoints,&Timestamp)[].[Timestamp,Average]' --output text
done
```

```
== CPUUtilization
2026-10-23T16:30:00+00:00  3.1
2026-10-23T16:40:00+00:00  98.7
== CPUCreditBalance
2026-10-23T16:30:00+00:00  140.2
2026-10-23T16:40:00+00:00  132.5
== CPUCreditUsage
2026-10-23T16:40:00+00:00  9.9
```

Credit arithmetic for t3.small: one credit = one vCPU at 100% for one minute. Two vCPUs at 100% spend 2 credits/minute (~10 per 5-minute period, as above) = 120/hour, while the instance earns 24/hour, so the balance falls by ~96/hour. Baseline = 24 / 60 / 2 vCPU = 20% per vCPU.

- **Standard mode:** at balance 0 the hypervisor caps the instance at 20%. CloudWatch `CPUUtilization` then sits at ~20% while Windows shows 100% (every runnable thread is waiting; the guest cannot see the cap). That is the ticket's discrepancy.
- **Unlimited mode (default for T3):** no cap; once the balance is gone, `CPUSurplusCreditBalance` rises and `CPUSurplusCreditsCharged` is billed if the 24-hour average exceeds baseline.

Change mode (only if that is the recommendation):

```bash
aws ec2 modify-instance-credit-specification \
  --instance-credit-specifications "InstanceId=$WIN_ID,CpuCredits=standard"     # or unlimited
```

## 8. Why the numbers differ / no memory metric

- CloudWatch `CPUUtilization` is measured by the hypervisor as a percentage of the instance's full vCPUs, averaged over 5 minutes (basic monitoring). Windows counters are 1-second guest views. Short spikes disappear in a 5-minute average; throttling is invisible to the guest.
- EC2 cannot see inside the guest, so memory, page file and disk-free metrics require the **CloudWatch agent** (publishing e.g. `Memory % Committed Bytes In Use`, `LogicalDisk % Free Space` to the `CWAgent` namespace). That needs `CloudWatchAgentServerPolicy` on the instance role, which this lab deliberately does not have - a good interview point.

## 9. Resolve and recommend

```powershell
# Use the PIDs you identified in steps 2-3. Do not pattern-match on "-EncodedCommand":
# other legitimate PowerShell processes (including agent-launched ones) may use it.
Stop-Process -Id 4120, 4388, 4602 -Force
Start-Sleep 10
Get-Counter '\Processor(_Total)\% Processor Time', '\Memory\Available MBytes' -SampleInterval 2 -MaxSamples 3
```

Expect CPU back under ~10% and Available MBytes back to roughly 800-1000 on an idle t3.small (Windows Server 2022 with Defender uses about 1 GB at rest).

Example recommendation:

```
Root cause: two runaway PowerShell loops (100% of both vCPUs) and a script holding 1.2 GB of RAM on a 2 GiB instance.
Evidence: processor queue 6-10, Available MBytes ~140, Pages/sec ~1800; CPUCreditBalance falling ~96/h.
Action taken: processes terminated; CPU 4%, 900 MB available.
Recommendations: fix/schedule the scripts properly; install the CloudWatch agent with memory and disk metrics
and alarms on CPUCreditBalance; if the steady-state load really exceeds the 20% baseline, move to a
non-burstable type (e.g. m6i.large) instead of paying T3 surplus credits; 2 GiB is below a comfortable
size for Windows Server with a GUI - t3.medium (4 GiB) minimum for real workloads.
```

## Cleanup / reset

```powershell
logman stop INC3302 2>$null
logman delete INC3302
Remove-Item C:\PerfLogs\INC3302* -Force
Get-Process powershell | Where-Object Id -ne $PID | Select-Object Id, StartTime   # make sure no hogs remain
```

If you switched the credit mode, set it back to what `describe-instance-credit-specifications` showed at the start.

## Common mistakes

- Sorting `Get-Process` by `CPU` and blaming the oldest process.
- Taking one `Get-Counter` sample (the first sample of a rate counter can be misleading) - always take several.
- Reading `% Processor Time` per process without dividing by the number of vCPUs (values up to 200% on 2 vCPU).
- Confusing working set with private bytes when hunting a leak.
- Killing `svchost` by highest memory without `tasklist /svc` to see which services live in it.
- Trusting CloudWatch CPU alone on a T3 without looking at `CPUCreditBalance`.
- Leaving a data collector running or the load running overnight in unlimited mode (real cost).
