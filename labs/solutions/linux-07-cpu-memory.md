# Solution — Linux 07: CPU and memory troubleshooting

## 0. Generate the problems

Session 1 — CPU (one burner per vCPU):

```bash
nproc
# 2
for i in 1 2; do nohup bash -c 'exec -a cpu-burner yes > /dev/null' >/dev/null 2>&1 & done
pgrep -a yes
# 4510 cpu-burner
# 4511 cpu-burner
```

`exec -a` sets the process name so it is easy to spot.

Session 2 — memory (no swap yet):

```bash
python3 - <<'EOF'
import time
chunks = []
for i in range(12):
    chunks.append(bytearray(100 * 1024 * 1024))   # 100 MiB, zero-filled and touched
    print(f"allocated {(i+1)*100} MiB", flush=True)
    time.sleep(2)
time.sleep(300)
EOF
```

Expected: progress to roughly 600–800 MiB, then `Killed`.

## 1. Who is using the CPU?

```bash
uptime
#  11:40:12 up 2:10,  2 users,  load average: 2.05, 1.86, 1.20
```

Load average ≈ number of runnable (plus uninterruptible) tasks. With 2 vCPUs, a load of
2.0 means fully busy; above 2 means queueing.

```bash
top -b -n 1 | head -15
```

```
%Cpu(s): 99.5 us,  0.5 sy,  0.0 ni,  0.0 id,  0.0 wa,  0.0 hi,  0.0 si,  0.0 st
    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND
   4510 ssm-user  20   0  220976   1792   1664 R  99.7   0.2   5:02.11 yes
   4511 ssm-user  20   0  220976   1792   1664 R  99.3   0.2   5:01.87 yes
```

`ps` view and per-CPU detail (`sysstat` is not installed by default):

```bash
ps -eo pid,user,%cpu,%mem,rss,etime,cmd --sort=-%cpu | head -5
sudo dnf install -y sysstat
mpstat -P ALL 2 3
pidstat -u 2 3
```

`st` (steal) matters on T instances: in **standard** mode, when credits are exhausted the
hypervisor throttles you to baseline and `%st` rises sharply. In **unlimited** mode you
keep full speed and pay for surplus credits instead.

Pressure stall information is a quick saturation signal:

```bash
cat /proc/pressure/cpu
# some avg10=48.20 avg60=45.11 avg300=30.02 total=...
```

## 2. Credit mode (workstation)

```bash
IID=i-0123456789abcdef0
aws ec2 describe-instance-credit-specifications --instance-ids $IID
# { "InstanceCreditSpecifications": [ { "InstanceId": "i-...", "CpuCredits": "unlimited" } ] }
```

T3 instances launch as `unlimited` by default unless the template sets otherwise.

t3.micro facts:
- Baseline: 10% per vCPU (20% of one vCPU-equivalent across the 2 vCPUs combined).
- Earns 12 credits/hour; max balance 288. One credit = one vCPU at 100% for one minute.
- Two vCPUs at 100% consume 120 credits/hour — 10x the earn rate.
- **Standard**: balance hits 0 → throttled to baseline; app becomes very slow.
- **Unlimited**: balance hits 0 → keeps bursting, `CPUSurplusCreditBalance` grows; surplus
  not paid back within 24 h is billed (Linux: $0.05 per vCPU-hour, region-dependent).

## 3. CloudWatch credit trend (workstation)

```bash
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 --metric-name CPUCreditBalance \
  --dimensions Name=InstanceId,Value=$IID \
  --start-time $(date -u -d '-1 hour' +%FT%TZ) --end-time $(date -u +%FT%TZ) \
  --period 300 --statistics Average \
  --query 'sort_by(Datapoints,&Timestamp)[].[Timestamp,Average]' --output table
```

Repeat with `CPUSurplusCreditBalance` and `CPUUtilization`. Basic monitoring publishes every
5 minutes; credit metrics may lag by a few minutes.

## 4. What the OOM killer did

```bash
sudo journalctl -k --since "-30 min" --no-pager | grep -iE 'out of memory|oom-kill|Killed process'
```

Expected:

```
kernel: python3 invoked oom-killer: gfp_mask=0x140dca(...), order=0, oom_score_adj=0
kernel: oom-kill:constraint=CONSTRAINT_NONE,...,task=python3,pid=4620,uid=1001
kernel: Out of memory: Killed process 4620 (python3) total-vm:812345kB, anon-rss:745120kB, file-rss:4kB, shmem-rss:0kB, UID:1001 pgtables:1600kB oom_score_adj:0
```

Why python3: the kernel kills the task with the highest `oom_score`, roughly its share of
RAM+swap adjusted by `oom_score_adj`. The biggest resident process loses.

```bash
for p in $(pgrep -f amazon-ssm-agent) $$; do echo "$p $(cat /proc/$p/oom_score) $(cat /proc/$p/comm)"; done
```

A process that "disappears without logging" and whose parent saw exit status 137
(128 + SIGKILL) is the OOM signature.

## 5. Reading `free`

```bash
free -h
#                total        used        free      shared  buff/cache   available
# Mem:           914Mi       310Mi       150Mi       0.0Ki       453Mi       470Mi
# Swap:             0B          0B          0B
```

- `total` is less than 1 GiB because the kernel reserves some memory.
- `free` is low by design: Linux uses idle RAM as page cache (`buff/cache`).
- **`available`** is the number to watch: memory obtainable without swapping.
- `Swap: 0B` — no swap by default on AL2023 EC2.

## 6. Contain a CPU hog without killing it

Lower priority of the running burners:

```bash
sudo renice -n 19 -p $(pgrep -d' ' -f cpu-burner)
top -b -n1 | grep -E 'NI|yes'
# NI = 19 now; they still use idle CPU but yield to normal-priority work
```

`renice` does not reduce total CPU use on an otherwise idle box, so credits still burn.
A hard cap does:

```bash
sudo systemd-run --scope -p CPUQuota=20% -p MemoryMax=200M --unit=capped-test \
  bash -c 'exec -a capped-burner yes > /dev/null' &
systemd-cgtop -b -n 2 | grep capped
# /system.slice/capped-test.scope   1   20.0   ...
systemctl status capped-test.scope --no-pager | grep -E 'CPU|Memory'
```

`CPUQuota=20%` = 20% of one CPU. `MemoryMax` makes the kernel OOM-kill inside that cgroup
only, protecting the rest of the host. For a permanent service, put these in the unit file
or a drop-in.

## 7. Add persistent swap

```bash
sudo dd if=/dev/zero of=/swapfile bs=1M count=1024 status=progress
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap defaults 0 0' | sudo tee -a /etc/fstab
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/90-swappiness.conf
sudo sysctl --system | grep swappiness

findmnt --verify --verbose | tail -3
swapon --show
# NAME      TYPE SIZE USED PRIO
# /swapfile file   1G   0B   -2
```

`dd` is used rather than `fallocate` because swap files must have no holes; on XFS
`fallocate` usually works on current kernels, but `dd` is always safe.

Re-run the memory test from step 0:

```
allocated 1200 MiB
```

It completes (slowly). In another session:

```bash
vmstat 2 5
#  r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st
#  1  1 412000  40000   1000  60000  120 4800   300  4900 ...
```

`si`/`so` (swap in/out) non-zero and `wa` rising = memory pressure being absorbed by disk.
This is why swap is a safety net, not capacity.

Reboot test (optional): `sudo systemctl reboot`, reconnect, then `swapon --show` and
`sysctl vm.swappiness`.

## 8. Stop the load

```bash
pkill -f cpu-burner
pkill -f capped-burner
sudo systemctl stop capped-test.scope 2>/dev/null
pgrep -a yes || echo "no burners left"
uptime          # load average decays over a few minutes
```

## Cleanup / reset

```bash
sudo swapoff /swapfile
sudo sed -i '\|^/swapfile |d' /etc/fstab
sudo rm -f /swapfile /etc/sysctl.d/90-swappiness.conf
sudo sysctl -w vm.swappiness=60
sudo dnf remove -y sysstat     # optional
```

Keep swap if you want a more forgiving lab host; it costs only root volume space.

## Common mistakes

- Reading `free` "free" column as available memory.
- Treating load average as a percentage; it must be compared to `nproc`.
- Forgetting `%st` — high steal on a T instance in standard mode means credit exhaustion,
  not your application.
- Assuming `nice` saves credits. Only a cap or less work does.
- Leaving burners running in unlimited mode and paying surplus charges.
- Creating a swap file with mode 644 (`mkswap` warns: insecure permissions).
- Editing `/etc/fstab` and rebooting without `findmnt --verify`.
- Looking for OOM messages in `/var/log/messages` (does not exist on AL2023; use `journalctl -k`).
