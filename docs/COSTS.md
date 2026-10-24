# Costs and budget controls

Prices are us-east-1 on-demand list prices, rounded. Check the
[AWS Pricing Calculator](https://calculator.aws/) for your region. Prices change.

## 1. What costs money

| Item | Rate | Running 24×7 / month | Charged while stopped? |
|---|---|---|---|
| Linux t3.micro | ~USD 0.0104/h | ~USD 7.60 | No |
| Windows t3.small (Windows licence included) | ~USD 0.0392/h | ~USD 28.60 | No |
| Public IPv4 address, 2 × USD 0.005/h | USD 0.010/h | ~USD 7.30 | No (auto-assigned IPs are released on stop) |
| gp3 storage: 20 + 40 + 5 + 5 = 70 GiB × USD 0.08 | — | ~USD 5.60 | **Yes** |
| Customer-managed KMS key (optional) | USD 1/month + requests | ~USD 1.00 | **Yes** |
| SSM interface endpoints (optional): 3 endpoints × 2 subnets × USD 0.01/h | USD 0.06/h | ~USD 43.80 | **Yes**. They are not instances, so stopping the servers does not stop them |
| EBS snapshots you take | ~USD 0.05/GiB-month (changed blocks) | varies | **Yes**. Destroy does **not** delete them |
| Data transfer out (patch downloads are inbound, which is free) | first 100 GB/month free | ~0 | — |
| Session Manager, Run Command, Fleet Manager, CloudFormation, IAM, VPC, IGW, S3 gateway endpoint | free | 0 | — |

**Totals with the defaults:**
- left running 24×7: about **USD 49/month**;
- 60 hours of practice in a month, stopped otherwise: 60 h × ~USD 0.06 + USD 5.60 ≈ **USD 9–10/month**;
- destroyed: about **USD 0** (only the bootstrap's few KB in S3, plus any snapshots you kept).

If your account is on the AWS Free Tier, t3.micro Linux hours and some EBS storage may be free.
Windows hours are not. Don't plan around the Free Tier: check your Billing console.

`CPUCredits: standard` is set on both servers. T3 instances otherwise default to *unlimited*
mode, where a process stuck at 100% CPU can build up surplus-credit charges. In standard mode the
CPU throttles to baseline instead.

## 2. Habits that keep it cheap

1. **Stop the servers at the end of every session**: Actions → stop-start → `stop`.
2. **Destroy** the stack if you won't practise for a week or more. Redeploying takes about 15 minutes.
3. Delete practice snapshots and extra volumes as soon as the exercise is done.
4. Leave `enable_ssm_vpc_endpoints` off unless you are doing that exercise, and redeploy without
   it straight afterwards.
5. Don't pick larger instance types and forget about them. `t3.medium` for Windows is reasonable
   while you are using the desktop, then go back to `t3.small`.

## 3. Find what is costing money

```bash
# Anything still tagged for the lab
aws resourcegroupstaggingapi get-resources --tag-filters Key=Project,Values=asrs-lab \
  --query "ResourceTagMappingList[].ResourceARN" --output table --profile asrs-admin

# Month-to-date spend by service (Cost Explorer must be enabled once; API calls cost USD 0.01 each)
aws ce get-cost-and-usage --time-period Start=$(date +%Y-%m-01),End=$(date -d tomorrow +%F) \
  --granularity MONTHLY --metrics UnblendedCost --group-by Type=DIMENSION,Key=SERVICE \
  --profile asrs-admin
```

To break costs down by `Project`, activate the tag: Billing → **Cost allocation tags** → select
`Project` → Activate. It only applies to costs incurred after activation.

## 4. Set a budget alert

**A budget is an alert, not a spending cap.** AWS Budgets emails you when actual or forecast spend
crosses a threshold. It does **not** stop, delete or block anything by default. Billing data also
lags by up to about a day, so you can overshoot before the email arrives. Your real controls are
stopping and destroying the lab.

### Console

1. Billing and Cost Management → **Budgets → Create budget**.
2. **Use a template → Monthly cost budget**.
3. Name `asrs-lab-monthly`, amount e.g. **USD 20**, your email address.
4. Create. The template alerts at 85% and 100% of actual spend and at 100% of forecast spend.

### CLI

```bash
ACCOUNT=$(aws sts get-caller-identity --query Account --output text --profile asrs-admin)
cat > budget.json <<'EOF'
{ "BudgetName": "asrs-lab-monthly", "BudgetType": "COST", "TimeUnit": "MONTHLY",
  "BudgetLimit": { "Amount": "20", "Unit": "USD" } }
EOF
cat > notifications.json <<'EOF'
[
 { "Notification": { "NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN",
     "Threshold": 50, "ThresholdType": "PERCENTAGE" },
   "Subscribers": [ { "SubscriptionType": "EMAIL", "Address": "you@example.com" } ] },
 { "Notification": { "NotificationType": "FORECASTED", "ComparisonOperator": "GREATER_THAN",
     "Threshold": 100, "ThresholdType": "PERCENTAGE" },
   "Subscribers": [ { "SubscriptionType": "EMAIL", "Address": "you@example.com" } ] }
]
EOF
aws budgets create-budget --account-id "$ACCOUNT" --budget file://budget.json \
  --notifications-with-subscribers file://notifications.json --profile asrs-admin
rm budget.json notifications.json
```

Alert-only budgets like this one are free. Budgets with actions (next section) are free for the
first two, then cost a small daily fee each.

### Optional: make the alert do something

Budgets can run a **budget action** at a threshold, for example applying an IAM policy or a
Service Control Policy that denies `ec2:RunInstances`, or stopping specific EC2 instances. This
needs an IAM role for AWS Budgets, and it still only reacts to delayed billing data. It is optional
here and is a good interview talking point ("alerts vs. guardrails").

Also worth turning on: Billing → Billing preferences → **Free Tier usage alerts**, and
**Cost Anomaly Detection** (free).
