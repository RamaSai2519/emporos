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
- It needs the worker to be running. The host is stopped when not in use, so someone has to start it
  before 09:15 on each trading day (below). Nothing starts it by itself.

## Turn it on

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

The files are local data and are not to be committed. The host has no SSH, so use a presigned upload:
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

- **Nothing starts the host or the worker by itself.** Recording needs a start each trading day. An
  EventBridge schedule that starts the instance around 08:45 IST, a systemd timer for the worker unit
  and a stop after 15:45 would make it unattended; that changes the "stopped when not in use" cost
  stance of the host, so it is not built.
- The paper worker also needs Atlas (its journals). That is unchanged by this change.
- Holiday detection is by the exchange timestamp of each quote, not a calendar.
- Which holdout names are recorded does not decide what research may read: the D1 seeded holdout
  rule still applies to any analysis of these files.
