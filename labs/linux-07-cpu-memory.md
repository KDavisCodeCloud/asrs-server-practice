# Linux 07 — CPU and memory troubleshooting

## Objective

Diagnose high CPU and memory pressure on a small burstable instance, identify the
offending processes, understand what the OOM killer did, and apply mitigations: process
priority/limits and swap.

## Scenario

> **Ticket INC-2102** — Users report `asrs-lab-linux` is "slow and sometimes kills things".
> CloudWatch shows CPU pinned near 100% for long stretches and a falling CPU credit
> balance. An overnight batch process disappeared without logging an error.
> The instance is a t3.micro: 2 vCPUs, 1 GiB RAM, no swap. Find what is consuming
> resources, explain the credit behaviour and the vanished process, and stabilise the host
> without resizing it.

## Prerequisites

- Session on `asrs-lab-linux` with sudo. Open **two** sessions: one to generate load, one to observe.
- AWS CLI on your workstation for CloudWatch/EC2 queries (read-only).
- Python 3 (present by default) for generating memory load.

**Setup (generate the problems yourself):**

1. CPU: start two CPU-bound background processes (one per vCPU), e.g. an infinite
   busy loop or `yes` redirected to `/dev/null`, and give them a recognisable name or note their PIDs.
2. Memory: in the second session, run a Python one-liner that allocates and holds roughly
   1.2 GB in steps of 100 MB, printing progress. Do this with no swap configured.
3. Leave the CPU load running for at least 15 minutes before checking CloudWatch.

## What you need to accomplish

1. Identify the top CPU consumers, their owners, and how the load average relates to the
   vCPU count.
2. Determine whether the instance is in standard or unlimited credit mode and explain what
   happens to performance or cost when credits run out in that mode.
3. Show the CPU credit balance trend from CloudWatch for the last hour.
4. Prove from kernel logs which process the OOM killer terminated, and why it chose it.
5. Interpret `free` output correctly (used vs available vs buff/cache).
6. Reduce the impact of a CPU hog without killing it, and run a command under a hard CPU
   and memory limit.
7. Add 1 GiB of swap that persists across reboots, with a conservative swappiness, and
   show the memory test now behaves differently.
8. Stop all load generators and confirm the host is back to idle.

## Commands or tools to research

- `top` / `htop` (keys `P`, `M`, `1`; fields `%st`, `%wa`, `load average`), `uptime`, `nproc`
- `ps -eo pid,user,%cpu,%mem,rss,cmd --sort`, `pidstat`, `mpstat`, `vmstat` (package `sysstat`)
- `free -h`, `/proc/meminfo`, `/proc/pressure/{cpu,memory}`
- `journalctl -k`, `dmesg`, `oom_score`, `oom_score_adj`
- `nice`, `renice`, `kill`, `systemd-run --scope` (`CPUQuota=`, `MemoryMax=`), `systemd-cgtop`
- `swapon`, `mkswap`, `dd`, `/etc/fstab`, `sysctl vm.swappiness`, `/etc/sysctl.d/`
- `aws ec2 describe-instance-credit-specifications`, `aws cloudwatch get-metric-statistics`
  (`CPUCreditBalance`, `CPUSurplusCreditBalance`, `CPUUtilization`)

## Validation criteria

- You can name the PIDs/commands responsible for CPU load and the memory spike.
- You can state the instance's credit mode and the t3.micro baseline (percentage per vCPU).
- CloudWatch credit balance data points retrieved for the past hour.
- A journal line from the kernel shows `Out of memory: Killed process <pid> (python3)`.
- `swapon --show` lists a 1 GiB swap file; `/etc/fstab` contains it; `sysctl vm.swappiness`
  returns your chosen value; the same remain after a reboot.
- `systemd-cgtop` or `systemctl status` of a transient scope shows the CPU/memory limit applied.
- After cleanup, load average trends toward 0 and no test processes remain.

## Recovery considerations

- On 1 GiB RAM the OOM killer can choose `amazon-ssm-agent` or your session's shell. If your
  session freezes or drops, wait a minute and reconnect; if the instance is unresponsive,
  stop/start it with the stop-start workflow.
- Unlimited mode can incur surplus credit charges if you leave CPU burners running for
  hours. Set a reminder to stop them; do not leave them over a stop/start (background
  processes do not survive a stop, but a persistent unit would).
- Swap on EBS is slow and consumes root volume space (keep 1 GiB on the 20 GiB root). Swap
  hides memory problems; it is a mitigation, not a fix.
- A wrong `/etc/fstab` line can stop the instance booting normally. Validate with
  `findmnt --verify` and `swapon -a` before rebooting; use `nofail` where appropriate.

**Interview angle:** "CPU at 100% on a T-family instance — what's different from an M-family
instance?" (Credits, baseline, steal time, unlimited-mode cost.)

[Solution](solutions/linux-07-cpu-memory.md) — try it first.
