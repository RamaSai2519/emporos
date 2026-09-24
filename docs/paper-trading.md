# Paper trading a strategy from the dashboard

Paper trading runs a strategy on the live market with simulated fills. It goes through exactly the
same signal, risk and execution path as live trading will; only the venue differs: the paper broker
keeps its own books (`paper_*` collections) and no order can reach Angel One.

Status of this page: **the paper worker has been tested with a scripted feed on real Atlas in virtual
time, and has NOT yet run on live NSE ticks** (the open-market smoke test is EM-138). The first
morning you run it is the smoke test; watch it.

## What runs where

| process | started with | holds Angel One credentials? |
|---|---|---|
| worker | `emporos worker run` (also `emporos run`) | yes, for **market data only**: quotes, history and the tick socket. It is handed a view of the broker with six market-data calls and no order method |
| API | `emporos api serve` | no; it reads what the worker persisted and records commands |
| dashboard | `cd dashboard && npm run dev` | no; it talks only to the API |

The dashboard and API never import broker code (an import contract and a fresh-interpreter test
enforce it). A "start strategy" click is a command: recorded once, claimed and executed by the worker.

## One-time setup

```bash
pipenv run emporos db migrate              # creates any missing collection or index
pipenv run emporos api set-passcode        # the single operator passcode (stored as an Argon2id hash)
```

In the repository `.env`: `API_JWT_SECRET` (at least 32 bytes), `API_ACCOUNT_ID=paper`,
`API_CORS_ORIGINS=http://127.0.0.1:3000,http://localhost:3000`, and the `ANGELONE_*` credentials.
In `dashboard/`: `cp .env.example .env.local` and `npm ci`.

## Each trading day

Start the worker **before 09:15 IST**. It runs one trading day: it recovers, reconciles, trades,
squares everything off at 15:15 and ends at the close. Start it again the next morning.

```bash
pipenv run emporos worker run                         # every config/strategies/*.yaml is loadable
pipenv run emporos api serve                          # second terminal
(cd dashboard && npm run dev)                         # third terminal, then open http://127.0.0.1:3000
```

With no `--start`, nothing trades until you start a strategy from the dashboard. Every loadable
strategy's universe is subscribed from the start, so a strategy you start at 11:00 has live data at
once, and it is warmed up first with the last closed bars (the same loader a backtest uses), so its
indicators are not cold.

To start one at the open without the dashboard: `emporos worker run --start orb_v1`. A strategy
that is not validated needs `--acknowledge orb_v1=rejected` (naming its standing, see below).

## Choosing a strategy on the Strategies page

Each strategy card shows, beside its start/stop control:

- **its status**: running if its latest run has not stopped, otherwise stopped;
- **its backtest verdict**: `validated`, `inconclusive`, `rejected`, `stale` or `no verdict`, judged
  against the config the strategy would run **today**, with the gates that failed, the number left
  unresolved, what history and capital it was judged on, and any note (for example that it was
  judged with smaller positions than its config uses).

The verdict is recorded by the curation run itself (`emporos backtest curate`, or
`emporos backtest verdicts import <report.json> --from ... --to ... --experiment ...` for a report
made before verdicts were recorded). Nobody types one in. `emporos backtest verdicts list` shows
every strategy's standing from the command line.

**A strategy graduated to `paper` (EM-189, `emporos graduation promote --to paper`) starts without
naming its standing**; anything else still needs it, as below. Graduation is per configuration, so
an edit puts it back to `research`.

**Starting a strategy that is not validated is allowed in paper, but never by accident.** The
dashboard asks you to type its standing (`rejected`, `inconclusive`, `stale`, `none`), and the worker
checks that word itself: the same start sent by any other client without it is refused, with the
reason. A `stale` verdict means the strategy's config was edited after it was judged: it is evidence
about the old config, not the new one. Editing `enabled` does not make a verdict stale; editing
anything that changes behaviour (parameters, universe, risk, execution, session) does.

Stop is graceful: the strategy stops signalling and its positions are **not** closed. Closing them is
a separate action (Close position, or Square off all).

## What to expect, and what is different from live

- Fills are simulated against live quotes with the platform's dated charges. They are not what a
  real broker would have given you.
- Risk uses `config/risk.yaml` (25,000 per instrument, 3 open positions, 50,000 deployed and a
  1,000 daily loss cap: aligned to the ₹50,000 capital by EM-189, values PROPOSED pending operator
  sign-off; the stricter live tier is in [live-trading.md](live-trading.md)). A backtest
  verdict is reached under limits and position sizes **scaled to the benchmark capital** (₹50,000,
  10% per position), so a strategy sized larger in its config will trade larger here than it was
  judged at. The verdict says so in its notes.
- The worker refuses UPDATE_STRATEGY_CONFIG (there is no config validator wired), and refuses the
  backfill and backtest commands: backtests are run from the command line only.
- The anomaly tripwire (EM-189) runs in paper exactly as in live: a feed down for a minute, a stuck
  UNKNOWN order, a rejection burst and so on halt new orders (exits still work) until
  `emporos resume`. It is exercised here first on purpose.
- The kill switch works independently of the dashboard: `emporos halt`, the file sentinel
  (`~/.emporos/HALT`, or `KILL_SWITCH_FILE`), and the Mongo flag.

## If something looks wrong

- The worker prints its final state when it ends. A non-zero exit means it failed; read the message.
- Controls are paused on the dashboard when the worker's state is stale: start the worker.
- `emporos backtest verdicts list` says `stale` for a strategy you just edited: expected.

## Did paper behave like the backtest? (parity)

After close-out the worker compares each strategy's day with a backtest of the identical config over
the same session and stores a daily report (a weekly one on the week's last session, and a
cumulative one that graduation reads). It is read-only, time-boxed and cannot affect the session;
`--no-parity` turns it off. See [paper-parity.md](paper-parity.md), and
`emporos paper parity daily|weekly|show|export`.
