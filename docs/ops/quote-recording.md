# L1 quote recording (D7, EM-217)

Every trading day the recorder is not running is a day of real spreads lost for good. In three to
six months the files make the microstructure lanes possible (L13 passive entries, the
quote-conditioned part of L9) and replace the cost model's assumed spread with a measured one.

## What it records

For each D1 name (the included names and the held-out ones: `config/universe/d1/universe.yaml`, about
214 instruments), once a minute between **09:15 and 15:30 IST on weekdays**:

| column | meaning |
|---|---|
| `instrument_id` | `NSE:<token>` |
| `received_at` | when the worker got the reply (UTC) |
| `exchange_ts` | the exchange's time of the last trade (UTC), null if not sent |
| `ltp`, `volume` | last price and day volume |
| `bid`, `ask` | best bid and ask; null when that side of the book is empty |
| `bid_qty`, `ask_qty` | quantity resting at the best bid and ask |

Prices are exact to the paisa (`decimal(14,2)`). Files are Parquet (zstd) under
`data/quotes/date=YYYY-MM-DD/part-<time>-<n>.parquet` (relative to the worker's working directory:
`/opt/emporos/app/data/quotes` on the host). About 214 x 375 = 80,000 rows a day, roughly 2 MB. A
quote whose exchange time is not today (an exchange holiday, a name that did not trade) is dropped
and counted. Nothing goes to Mongo.

## What it can and cannot do

- **Read-only.** It runs as one more scheduled job (`quote_recorder`) in the worker's own loop, over
  the worker's own broker session. It is handed the market-data calls and nothing else
  (`MarketDataOnly`), and the package `emporos.quotes` is forbidden by an import-linter contract from
  importing order, execution, risk, strategy, storage or concrete-broker code. It cannot place,
  cancel or modify an order, and it does not open a second broker session.
- **Orders come first.** The execution gateway counts order calls in flight. A poll that finds one
  does not start, and a poll in progress stops after its current batch.
- **Gentle on the broker.** At most 50 symbols per request (5 requests a poll, so 5 requests a
  minute against the quote group's 10/s, 500/min, 5000/hour limits, which the same rate limiter
  enforces). A poll is capped at 20 s and each request at 8 s, so a slow broker cannot hold up the
  fills, reconciliation or kill-switch jobs that share the loop. On a rate-limit reply (Angel One's
  plain-text HTTP 403, or 429) it stops polling and waits 60 s, then 120 s, 240 s, and so on up to
  15 minutes, and a successful poll resets the wait. Other broker errors are counted and logged and
  never raise.
- **Crash-safe.** Rows are written every 10 polls and at 15:30 as new files (written to a temporary
  name, then renamed). A file is never rewritten. A crash loses at most the last ten minutes; a
  restart resumes cleanly, and the new files sit beside the old.
- Unattended, it is the `emporos-quotes` unit below; alongside a paper worker it is the
  `--record-quotes` flag. Either way something has to be running from before 09:15 IST.

## Unattended recording (EM-236): apply, verify, disable

Recording runs by itself every weekday: EventBridge Scheduler starts the instance at **08:40 IST** and
stops it at **15:50 IST**; `emporos-quotes.service` starts at boot, waits for 09:15, records until
15:30, writes the last files, uploads the day to S3 and exits. It runs `emporos worker record-quotes`,
which is **quotes only**: no strategy, no risk engine, no execution, no Mongo, never live, and the one
broker call it can make is the quote endpoint (a test pins that the whole day touches only login,
quotes and logout). `emporos-worker.service` stays disabled and is not touched by any of this.

What it does on its own: a weekend start logs in to nothing and exits 0; an exchange holiday (three
polls in a row with every quote stamped an earlier day) writes and uploads nothing and exits 0, and the
instance is stopped at 15:50 as usual; a crash mid-day restarts the recorder (new part files beside the
old); a stop signal writes what is in memory and uploads before exiting.

**IAM: no change needed.** Checked on 2026-09-25: the instance role `emporos-worker-role` already has
`s3:PutObject`, `s3:GetObject` and `s3:ListBucket` on `emporos-cold-135808951082-ap-south-1`, and the
host's `S3_BUCKET` parameter points at it. Files land at
`s3://emporos-cold-135808951082-ap-south-1/quotes/date=YYYY-MM-DD/part-*.parquet`.

The one-session rule: Angel One keeps one session per client code, so the recorder and the trading
worker must not run together (each login kills the other's session). The unit skips its start while
`emporos-worker` is active; to run the worker on a trading day, `sudo systemctl stop emporos-quotes`
first, and pause the stop schedule (State=DISABLED below) or the instance is stopped at 15:50 under
it. **Nobody logs in to Angel One from the dev machine between 08:30 and 16:00 IST on a weekday**: it
would end the host's session and lose that day's quotes from that minute.

### Apply (the operator, in this order)

Needs credentials that can create a CloudFormation stack with a named IAM role and EventBridge
schedules (the `journeymen` operator policy is SSM and start/stop only, so use an admin profile for
step 2).

```bash
export AWS_REGION=ap-south-1
export EMPOROS_INSTANCE=i-0cec4ddd7cdd5f96d

# 1. Get the code onto the host (after this change is pushed to origin). Start the instance if it is
#    stopped (production-host.md, "Start"), then, in a Session Manager shell:
aws ssm start-session --target $EMPOROS_INSTANCE
sudo -u emporos -H bash -c 'cd /opt/emporos/app && git pull --ff-only && /opt/emporos/.local/bin/pipenv sync'
sudo -u emporos -H git -C /opt/emporos/app log --oneline -1      # matches what was pushed

# 2. Deploy the schedule stack (from the repo checkout on your machine)
aws cloudformation deploy --stack-name emporos-quote-recording \
  --template-file infra/quote-recording-schedule.yaml --capabilities CAPABILITY_NAMED_IAM

# 3. Install and enable the unit on the host (same session as step 1). Enabling starts nothing now.
sudo install -m 644 /opt/emporos/app/infra/systemd/emporos-quotes.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable emporos-quotes
systemctl is-enabled emporos-quotes emporos-worker             # enabled, disabled
sudo -u emporos -H bash -c 'cd /opt/emporos/app && /opt/emporos/.local/bin/pipenv run emporos worker record-quotes --help'
```

If step 3 happens on a weekday before 09:15 IST you can start it at once
(`sudo systemctl start emporos-quotes`); otherwise leave the instance to be stopped by hand and the
schedule brings it up at the next 08:40.

### Verify

```bash
aws scheduler get-schedule --name emporos-quotes-start --query '[State,ScheduleExpression,ScheduleExpressionTimezone]'
aws scheduler get-schedule --name emporos-quotes-stop  --query '[State,ScheduleExpression,ScheduleExpressionTimezone]'
```

On the first trading morning (about 08:50 IST, over Session Manager):

```bash
systemctl status emporos-quotes                     # active (running); waiting for 09:15 is normal
journalctl -u emporos-quotes -f                     # no errors; "quote recorder rate limited" only occasionally
sudo -u emporos -H bash -c 'cd /opt/emporos/app && /opt/emporos/.local/bin/pipenv run emporos quotes summary'
```

After 15:50 the instance is stopped. From your machine, check the day landed:

```bash
aws s3 ls s3://emporos-cold-135808951082-ap-south-1/quotes/date=$(date +%F)/ | wc -l    # 37 or 38 files
```

A healthy day is in "Check it is working" above. `journalctl -u emporos-quotes` ends with
`quote recording day recorded: RecorderCounters(...)` and `quote upload: DayUpload(uploaded=N ...)`.
A holiday reads `day holiday` and uploads 0. An `upload_failed` above 0 exits with status 2 and the
unit retries; a re-run sends only what is missing.

### Disable, pause, remove

```bash
# Pause the schedules (the instance stays as it is); resume with State=ENABLED
aws cloudformation deploy --stack-name emporos-quote-recording \
  --template-file infra/quote-recording-schedule.yaml --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides State=DISABLED

# Stop the recorder on the host and keep it from starting at boot
sudo systemctl disable --now emporos-quotes

# Remove everything (the schedules and their role); the recorded files stay on the host and in S3
aws cloudformation delete-stack --stack-name emporos-quote-recording
sudo systemctl disable --now emporos-quotes && sudo rm /etc/systemd/system/emporos-quotes.service && sudo systemctl daemon-reload
```

Cost: the instance now runs about 7 hours on each weekday (about $0.02 an hour, roughly $3 a month)
instead of being stopped, including exchange holidays.

## Option quotes (EM-246, plan 7a)

`worker record-quotes` also records L1 quotes of near-the-money options, by default (`--no-record-options`
turns it off), into their OWN files: `data/quotes-options/date=YYYY-MM-DD/part-*.parquet`, uploaded to
`s3://<bucket>/quotes-options/date=YYYY-MM-DD/`. The stock-quote files and their readers are unchanged.

| what | rule |
|---|---|
| NIFTY and BANKNIFTY | the nearest 2 expiries, the at-the-money strike and 5 listed strikes either side, calls and puts (44 contracts each) |
| 20 stock options | the nearest expiry, ATM and 2 strikes either side, calls and puts (10 contracts each); the names are the 20 most traded in `fo_stock_v1` and are FIXED in `config/universe/quotes/option-underlyings.yaml` |

Columns: `instrument_id` (`NFO:<token>`), `underlying`, `expiry`, `strike`, `right` (CE/PE), `lot_size`,
`spot` (the underlying price the strike set was built from), `received_at`, `exchange_ts`, `ltp`, `bid`,
`ask`, `bid_qty`, `ask_qty`, `volume`, `open_interest`. About 290 contracts a minute, roughly 6 extra
quote requests a minute beside the 5 for the stock names (limit: 10/s, 500/min, 5,000/h).

How it behaves: tokens come from the public scrip master (downloaded once a day, only NFO option rows of
these underlyings kept); the strike set is rebuilt from a fresh spot every 15 minutes; the options are
polled only AFTER the stock quotes in the same minute and only while the stock recorder is not backed
off, so the D1 quotes always come first. On a rate-limit reply the options poll less often (the interval
doubles up to 5 minutes and halves again after 30 clean polls), independent of the stock recorder's
own back-off. A scrip master that will not load, or a spot that fails, keeps the previous set and is
retried; none of them raises. Quotes only: the process still reaches Angel One at login, quotes and
logout (a test pins it). BANKNIFTY weekly options no longer exist (monthly only), so its "nearest 2
expiries" are two monthlies.

Refresh the stock list monthly on the dev machine, then commit it:
`python -m emporos.cli research option-universe` (options `--count`, `--sessions`).

**IAM: no change needed** (the role has `s3:PutObject`/`GetObject`/`ListBucket` on the whole bucket).

## Turn it on (with a paper worker)

The flag exists on both worker commands: `emporos worker run --record-quotes` (paper) and
`emporos worker live --record-quotes`. Options: `--quotes-dir PATH` (default `data/quotes`) and
`--quote-interval SECONDS` (default 60, at least 30).

On the production host the worker unit runs `emporos worker run`. Use a drop-in so the unit file
stays as installed (see `docs/ops/production-host.md`, "Connect", for the session):

```bash
sudo systemctl edit emporos-worker
# [Service]
# ExecStart=
# ExecStart=/opt/emporos/.local/bin/pipenv run emporos worker run --record-quotes
```

Then bring the worker up **before 09:15 IST**, as in `production-host.md` section D:

```bash
sudo systemctl start emporos-worker
journalctl -u emporos-worker -f
```

Note the standing caveats from `production-host.md`: the worker has not yet run on the host, so the
first day is also the first-run checks (CloudWatch stream, Angel One login from the registered IP).
Deploy this change first: `git pull --ff-only && pipenv sync` as the `emporos` user (section C, "Update the code").

## Check it is working

After the first minutes of the session, and again after 15:30:

```bash
sudo -u emporos -H bash -c 'cd /opt/emporos/app && /opt/emporos/.local/bin/pipenv run emporos quotes summary'
```

A healthy full day shows about 375 polls, 200+ instruments, 75,000+ rows in 37 or 38 files, a
two-sided book share above 95% and a median spread of a few basis points (a few tens for mid-caps).
`--day YYYY-MM-DD` reads an earlier day. Worker log lines to know:

| log line | meaning |
|---|---|
| `quote recorder rate limited: backing off ...` | Angel One replied 403 or 429; it will retry later. Occasional is fine; a run of them all day means something else is using the quota |
| `quote recorder: <error>` | a batch failed (network, protocol); the next poll tries again |
| `quote recorder day summary: RecorderCounters(...)` | written once at 15:30: polls, rows, stale quotes dropped, times it yielded to an order, backed off, rate limited, errors, timeouts |
| `job_failed:quote_recorder` alert | the sink could not write (disk full or permissions): fix the disk, the job retries next interval |

`df -h /opt/emporos` should be checked once: the data is about 2 MB a day, 0.5 GB a year.

## Getting the files off the host

Unattended days are uploaded to S3 by the unit itself (above). Download a month with
`aws s3 sync s3://emporos-cold-135808951082-ap-south-1/quotes/ data/quotes/`. For files from a
manual run, use a presigned upload:
from a machine with the `journeymen` credentials create a presigned PUT for a bucket you own, run
the upload on the host with `curl -T`, and download from S3:

```bash
# on your machine
python3 - <<'PY'
import boto3
print(boto3.client("s3", region_name="ap-south-1").generate_presigned_url(
    "put_object", Params={"Bucket": "<bucket>", "Key": "quotes/2026-09.tar.gz"}, ExpiresIn=3600))
PY
# on the host (through SSM), with that URL
sudo -u emporos -H bash -c 'cd /opt/emporos/app/data && tar czf /tmp/quotes.tar.gz quotes && curl -sS -T /tmp/quotes.tar.gz "<url>"'
```

Do this monthly. Keep the originals on the host.

## Stopping it

Remove `--record-quotes` (delete the drop-in: `sudo systemctl revert emporos-worker`) and restart
the worker, or just stop the worker; the recorder flushes what it holds at 15:30 and on a clean stop
loses at most ten minutes of rows.

## What is not done, and needs the operator

- The stack and the unit are built and tested in the repository; **the operator applies them** (above).
- Holiday detection is by the exchange timestamp of each quote, not a calendar.
- Which holdout names are recorded does not decide what research may read: the D1 seeded holdout
  rule still applies to any analysis of these files.
