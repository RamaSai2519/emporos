# Production host: start, stop and access

One EC2 instance runs the trading worker. It is **stopped when not in use** to save cost.
The host was handed over and verified under EM-211 (closed 2026-09-25). What to do when it is
needed again is in [Bringing the host back](#bringing-the-host-back-live-testing-and-going-live).

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

To run the worker, when you decide to (see the runbook below first):

```bash
sudo systemctl start emporos-worker      # pulls in emporos-env first
journalctl -u emporos-worker -f
```

The unit's command is `emporos worker run`, which is the **paper** worker (real market data, no
real orders). It is not a live worker; see "Going live" below.

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

## State of the host (as of 2026-09-25, EM-211)

Everything below was done once and verified. Do not redo it; only re-check it if something fails.

| Item | State |
|---|---|
| Angel One SmartAPI key | Static IP `65.0.238.146` registered (operator, SmartAPI portal, 2026-09-25). Order endpoints accept the key only from this host; market data and login work from anywhere. Nothing has been ordered from it yet, and the verification ledger still reads `static_ip_registered` = BLOCKED (see `docs/live-trading.md`) |
| One login only | The host and your laptop use the same client code, and Angel One keeps one session per client code. While the worker runs on the host, do not run `test:live`, the recorders or any live script locally: each login invalidates the worker's session. Stop the worker first |
| MongoDB Atlas | `65.0.238.146/32` on the IP access list. Verified: an authenticated `ping` from the host to `emporos_dev` worked (84 collections) |
| GitHub | Read-only deploy key `emporos-worker-deploy` on `RamaSai2519/emporos`. It works over `ssh.github.com:443`; port 22 egress is closed by design |
| Code | `/opt/emporos/app`, cloned from `origin`, owned by `emporos`. Host HEAD was `55e9528`; whatever is pushed to `origin` is what the host gets |
| Python env | `pipenv sync` (**production dependencies only**). Dev tools and the dev-only SmartAPI SDK are deliberately absent, so lint, mypy and the test suite do not run on the host. Run them on your own machine before pushing |
| SSM parameters (`ap-south-1`) | SecureString: `MONGO_URL`, `ANGELONE_CLIENT_CODE`, `ANGELONE_PASSWORD`, `ANGELONE_TOTP_SECRET`, `ANGELONE_API_KEY`. String: `ENV=dev`, `LOG_LEVEL=INFO`, `S3_BUCKET=emporos-cold-135808951082-ap-south-1` |
| `emporos-env` | Verified: writes those 8 variables to `/run/emporos/env` (tmpfs, mode 600, owner `emporos`) |
| Kernel | 7.0.0-1013 (already rebooted onto it) |
| Alerts | SNS topic `emporos-alerts` emails the operator, but as of 2026-09-25 the subscription is still **PendingConfirmation**: click the link in the AWS confirmation email, or no alert is delivered. The budget `emporos-monthly` emails the operator directly and does not need it |
| `emporos-worker`, `emporos-env` | **disabled**. Never started as a service. The worker has never run on this host |

Open items at close (2026-09-25):

- **Confirm the SNS subscription** (see "Alerts" above). Check with
  `aws sns list-subscriptions-by-topic --region ap-south-1 --topic-arn <emporos-alerts ARN>`; the
  subscription ARN must not read `PendingConfirmation`.

Not yet verified on the host, because the worker has never run there:

- CloudWatch log streams. The log group `/emporos/worker` exists but has no streams until the worker
  writes logs. Confirm this on the first worker run (stage D, step 2).
- The commands in "Kill switch on the host" below (marked untested).

## Bringing the host back: live testing and going live

Work top to bottom. Stages A to C are the same for both; D is paper/live testing; E is going live.

### A. Before you start the instance (on your machine)

1. **Push what the host should run.** The host pulls from `origin`, not from your laptop. Check
   `git log origin/master..master`: anything listed there is not on the host.
2. **Run the full local gate** (`pipenv run lint`, `typecheck`, `test`, `coverage-gate`,
   `lint-imports`). The host cannot run it.
3. **Decide `ENV`.** `dev` uses `emporos_dev` (shared Atlas dev database); `main` uses production
   `emporos`. Live testing stays on `dev`. Only change it at go-live (stage E).
4. **Pick the time.** For anything that depends on the opening minutes, have the box up and the
   worker started **before 09:00 IST**. Starting mid-session makes `market_data.no_ticks_at_open`
   fire as a known false positive (see `docs/live-trading.md`, EM-186).

### B. Start and check the box

Start the instance and wait for SSM Online (commands in "Start" above). Then confirm the public IP is
still `65.0.238.146` (it always should be) and that nothing is running by itself:

```bash
aws ssm start-session --target $EMPOROS_INSTANCE
systemctl is-active emporos-worker emporos-env     # both: inactive
systemctl is-enabled emporos-worker emporos-env    # both: disabled
```

If the Angel One key's static IP was changed in the SmartAPI portal since last time, put
`65.0.238.146` back before anything else: order endpoints refuse any other address. Angel One
allows this to change only once a week.

### C. Update the code

Inside the session:

```bash
sudo -u emporos -H bash -c 'cd /opt/emporos/app && git pull --ff-only && /opt/emporos/.local/bin/pipenv sync'
sudo -u emporos -H git -C /opt/emporos/app log --oneline -1     # should match what you pushed
```

`pipenv sync` installs from `Pipfile.lock` with production packages only. Do not add `--dev` on this
host: it would put the SmartAPI SDK, a dev-only test oracle, on the machine that trades.

If you changed a secret in `.env` locally, re-upload it (the host reads SSM, never your `.env`). In
zsh, indirection is `${(P)n}`, not bash's `${!n}`:

```zsh
set -a; source .env; set +a
v="${(P)n}"    # inside a loop over the names; add --overwrite to replace an existing parameter
aws ssm put-parameter --region ap-south-1 --type SecureString --overwrite --name /emporos/$n --value "$v"
```

Values must not contain quotes or newlines, or `emporos-env` refuses to run.

### D. Live testing (paper worker on the real feed, `ENV=dev`)

This is what "live testing" means today: `emporos worker run` against the real Angel One market
feed with no real orders. It is what the unit already runs.

1. Start it and watch it:
   ```bash
   sudo systemctl start emporos-worker
   journalctl -u emporos-worker -f
   ```
2. **First time only: confirm CloudWatch.** Within a few minutes a log stream should appear in the
   log group `/emporos/worker` (console: CloudWatch > Log groups, or
   `aws logs describe-log-streams --log-group-name /emporos/worker --region ap-south-1`). This is the
   one EM-211 check that could not be done before the worker ran.
3. Let it run for the window you need. Check candles and paper activity in `emporos_dev`.
4. Stop it before stopping the instance:
   ```bash
   sudo systemctl stop emporos-worker
   ```
   Then remove the secrets file (it is on tmpfs and disappears on stop, but this is explicit):
   `sudo rm -f /run/emporos/env`.
5. Stop the instance (see "Stop" above).

### E. Going live

Do not start here. The conditions to be allowed to go live are in `docs/live-trading.md` ("The
gate", "What you must do, in order", and the compliance checklist): a validated strategy, paper
trading forward, graduation to `live_conservative`, a typed human acknowledgement, and the broker
verification checks. `emporos worker run-live -s <strategy>` prints every reason a strategy may not
go live and never places an order; run it first. As of 2026-09-25 none of the strategies pass.

When the gate passes, these are the **host** changes still needed. None is done yet.

1. **`/emporos/ENV`: `dev` to `main`.** This switches the worker from `emporos_dev` to the
   production database `emporos`. Do it deliberately, on the day:
   ```bash
   aws ssm put-parameter --region ap-south-1 --type String --overwrite --name /emporos/ENV --value main
   ```
2. **Add `/emporos/LIVE_TRADING_ENABLED`** (String, value `true`), on the day, after the strategy's
   `enabled: true` is committed and pushed. Set it back to `false` (or delete it) when the session is
   over. `emporos-env` loads every `/emporos/*` parameter into the worker's environment, and the
   risk engine blocks every live order while it is not `true`.
3. **Change the unit's command.** The installed unit runs `emporos worker run` (paper). Live is
   `emporos worker live -s <strategy>`. Use a drop-in so the unit file stays as installed:
   ```bash
   sudo systemctl edit emporos-worker
   # [Service]
   # ExecStart=
   # ExecStart=/opt/emporos/.local/bin/pipenv run emporos worker live -s <strategy>
   ```
   Also review `Restart=always` in that unit first: a live session that halts itself (the tripwire,
   or the kill switch) would otherwise be restarted after 10 seconds by systemd. The launch gate
   refuses to start with the kill switch set, but decide consciously whether a restart loop is what
   you want on a live day.
4. **Broker headers.** The client sends `X-ClientPublicIP`, `X-ClientLocalIP` and `X-MACAddress`
   from the settings `ANGELONE_CLIENT_PUBLIC_IP`, `ANGELONE_CLIENT_LOCAL_IP` and
   `ANGELONE_CLIENT_MAC_ADDRESS`. Unset, they default to `127.0.0.1` placeholders, which are fine
   for market data but, per `core/config.py`, "must be the worker's real ones for orders". They are
   not in SSM today. Add them as String parameters before the first order (values as of
   2026-09-25; re-read them from EC2 if the instance is ever replaced):
   ```bash
   aws ssm put-parameter --region ap-south-1 --type String --name /emporos/ANGELONE_CLIENT_PUBLIC_IP --value 65.0.238.146
   aws ssm put-parameter --region ap-south-1 --type String --name /emporos/ANGELONE_CLIENT_LOCAL_IP  --value 172.31.40.62
   aws ssm put-parameter --region ap-south-1 --type String --name /emporos/ANGELONE_CLIENT_MAC_ADDRESS --value 02:ff:dc:09:fc:a7
   ```
   (`172.31.40.62` is the instance's private IP and `02:ff:dc:09:fc:a7` the MAC of its network
   interface `eni-0c26dfa5bd63f224f`.) MAC is not an authorisation factor
   (`docs/live-trading.md`); the registered public IP is what the order endpoints check.
5. **First day small.** Use the tiny-limit-order round trip from `docs/live-trading.md` before any
   real size. Keep the kill switch and the instance stop within reach the whole session.

### Kill switch on the host

The kill switch does not depend on the dashboard. Four routes, fastest last:

1. `emporos halt` (CLI). Writes the file sentinel and the Mongo flag; it succeeds if at least one
   place took it. `emporos kill-switch status` reports each place, `emporos resume` clears it.
2. The Mongo flag (same commands).
3. The file sentinel (the path is `KILL_SWITCH_FILE`; default in `core/config.py`).
4. **Stop the instance:** `aws ec2 stop-instances --instance-ids $EMPOROS_INSTANCE`. It works even
   when everything else is down, but any live order already resting at the broker stays there.

Running the CLI on the box (**untested on the host**: the invocation below mirrors how the worker
unit loads its environment; try `kill-switch status` on the first start and correct this section if
it differs):

```bash
sudo systemctl start emporos-env     # if /run/emporos/env does not exist yet
sudo systemd-run --wait --pipe --collect -q -p User=emporos \
  -p EnvironmentFile=/run/emporos/env -p WorkingDirectory=/opt/emporos/app \
  -p Environment=HOME=/opt/emporos \
  /opt/emporos/.local/bin/pipenv run emporos kill-switch status
```

Replace `kill-switch status` with `halt -r "reason"` to halt. The `emporos` CLI also works from your
own machine against the same Mongo database, which is the more practical route for `halt` when the
box is fine and you just want trading to stop.

### Quick reference

| I want to | Do |
|---|---|
| Just look at the box | Start, `aws ssm start-session`, look, stop |
| Run the paper worker on real data | A, B, C, then D |
| Run live | A, B, C, then E (after the gate passes); D's monitoring and stop steps still apply |
| Stop trading now | `emporos halt`; if that fails, `aws ec2 stop-instances` |
| Change a secret | Update `.env`, re-upload (C), restart the worker |
| Rotate the deploy key | GitHub > Deploy keys, and `/opt/emporos/.ssh/` on the box (`github_deploy`) |
