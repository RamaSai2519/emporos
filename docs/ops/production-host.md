# Production host: start, stop and access

One EC2 instance runs the trading worker. It is **stopped when not in use** to save cost.
Tracking ticket for the remaining operator steps: EM-211.

| | |
|---|---|
| Account / region | 135808951082 / ap-south-1 |
| Instance | `i-0cec4ddd7cdd5f96d` (`emporos-worker`, t4g.small, Ubuntu 24.04 arm64) |
| Elastic IP | `65.0.238.146` (`eipalloc-090113bff4c47c572`), **never release or re-associate** |
| Shell access | SSM Session Manager only (no SSH, no key pair, no inbound rules) |
| Worker unit | `emporos-worker.service`, installed **disabled**; it never starts by itself |

All commands below run from a machine with the `journeymen` IAM user credentials
(policy `emporos-operator-ssm`). Session Manager needs the
[session-manager-plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
installed locally.

```bash
export AWS_REGION=ap-south-1
export EMPOROS_INSTANCE=i-0cec4ddd7cdd5f96d
```

## Start

```bash
aws ec2 start-instances --instance-ids $EMPOROS_INSTANCE
aws ec2 wait instance-running --instance-ids $EMPOROS_INSTANCE

# The box is usable once SSM reports it Online (usually 1-2 minutes after "running")
until [ "$(aws ssm describe-instance-information \
    --filters Key=InstanceIds,Values=$EMPOROS_INSTANCE \
    --query 'InstanceInformationList[0].PingStatus' --output text)" = "Online" ]; do
  sleep 10
done
echo online
```

Or in the console: EC2 > Instances > `emporos-worker` > Instance state > Start instance.

The public IP is always `65.0.238.146`. Starting does not change it, so the Angel One and Atlas
allowlists stay valid.

## Connect

```bash
aws ssm start-session --target $EMPOROS_INSTANCE              # interactive shell
sudo -u emporos -H bash                                        # inside the session, to act as the app user
```

One-off command without a shell:

```bash
aws ssm send-command --instance-ids $EMPOROS_INSTANCE \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["systemctl is-active emporos-worker"]'
# then read the result with the returned CommandId:
aws ssm get-command-invocation --command-id <id> --instance-id $EMPOROS_INSTANCE
```

## What happens on start

- Nothing trades. `emporos-worker` and `emporos-env` are disabled, so a start or reboot leaves the
  worker down.
- `/run/emporos/env` is on a tmpfs and is wiped on every stop. It is rebuilt from SSM
  (`/emporos/*`) by `emporos-env` each time the worker starts, so secrets are never on disk.
- Time sync (chrony to Amazon Time Sync), the CloudWatch agent and the swap file come back on
  their own.

To run the worker, when you decide to (after EM-211 is complete):

```bash
sudo systemctl start emporos-worker      # pulls in emporos-env first
journalctl -u emporos-worker -f
```

Do not `systemctl enable` it without deciding that the worker should start on every boot.

## Stop

Before stopping, make sure the worker is idle: no live session and no open orders or positions.
Stopping is also one of the kill-switch routes, but a stop mid-session leaves any live orders
resting at the broker.

```bash
sudo systemctl stop emporos-worker       # on the box, if it is running
aws ec2 stop-instances --instance-ids $EMPOROS_INSTANCE
aws ec2 wait instance-stopped --instance-ids $EMPOROS_INSTANCE
```

Termination protection is on. `stop` is allowed; terminating is not, and should never be needed.

## Cost while stopped

You stop paying for instance hours (about $0.02 an hour) and the swap and memory footprint. You
still pay for:

- the 30 GiB gp3 root volume (roughly $2.50 a month),
- the public IPv4 address (about $3.65 a month, charged whether or not it is attached),
- CloudWatch log storage, which is negligible.

Keeping the Elastic IP is intentional: Angel One lets its registered static IP change only once
a calendar week.

## Troubleshooting

- **SSM never shows Online:** check the instance status checks in the console, then that the
  security group still allows outbound 443. The agent reaches SSM over 443 only.
- **`ec2:StartInstances` access denied:** the operator policy only covers this instance ID. If the
  instance is ever replaced, update `emporos-operator-ssm` on the `journeymen` user.
- **Package installs fail with timeouts:** apt is configured for HTTPS
  (`https://ports.ubuntu.com/ubuntu-ports`) because the security group has no port 80 egress.
