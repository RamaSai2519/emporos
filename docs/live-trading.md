# Live trading: what exists, what does not, and what only you can do

**Live trading cannot be turned on today, even with every flag set.** The gate in front of it is
built and tested, and the live worker behind the gate is now built too (EM-142) — but nothing in
this repository has ever touched a real Angel One order endpoint. The only thing that has
exercised the Angel One order path, at every layer including the full worker composition, is an
emulator written from the documentation. No strategy is validated, no strategy has been paper
traded forward, and the dev API key has no static IP registered, so order endpoints would refuse
it even if every flag were set.

This page describes the gate as built, the composition behind it, and the steps that are yours.

## The gate (built, tested, off by default)

`emporos worker run-live -s <strategy>` asks whether a strategy may go live and prints **every**
reason it may not. It never starts a worker, never logs in to Angel One and never places an order —
that contract is frozen on purpose, as a safe dry-run an operator can always reach for. It exits 1
when something is missing, and 2 (never 0) when everything holds, because it deliberately never
proceeds to a live session itself. `emporos worker live -s <strategy>` runs the identical check and,
only when every condition holds for every named strategy, builds and runs the live composition
(EM-142, below) instead of stopping there. Run against the dev database today, `orb_v1` answers:

```
orb_v1: may NOT go live
  - LIVE_TRADING_ENABLED is not set to 'true'
  - orb_v1 has `enabled: false` in its config
  - orb_v1 is rejected, not validated
  - orb_v1 is at research for this configuration; it must be graduated to live_conservative (`emporos graduation promote`)
  - no human acknowledgement is recorded for orb_v1 in this configuration (`emporos graduation acknowledge orb_v1`)
```

The conditions, each its own small class in `session/launch_gate.py`, all of which must hold:

| condition | what satisfies it | who does it |
|---|---|---|
| the switch | `LIVE_TRADING_ENABLED=true` in the environment | you |
| the strategy is enabled | `enabled: true` in `config/strategies/<name>.yaml` | you; a committed decision recorded in `docs/strategies/`, never a default |
| a verdict was recorded, and it is validated | `emporos backtest curate` concluded `validated` **for exactly this config** | the curation, not you |
| the verdict is not stale | the config's behaviour has not changed since it was judged | you, by not editing it (editing `enabled` is fine) |
| the kill switch is clear | not set, and readable | you |
| graduated to `live_conservative` (EM-189) | the graduation ledger holds this **configuration** at `live_conservative` | `emporos graduation promote`, on evidence |
| a human acknowledged it (EM-189) | a `LiveAcknowledgement` for this configuration exists | you, typing a phrase at a terminal |
| the right risk tier (EM-189) | the worker loaded `config/risk.live_conservative.yaml` | the live worker does, always |

There is no acknowledgement that gets past live: the typed-standing escape that paper allows does
not exist here. A live start with any condition failing is refused, with all the failing reasons
together.

Independently of this launch check, the risk engine's `TradingModeGuard` blocks every order in live
mode while `LIVE_TRADING_ENABLED` is not true, so the switch is checked twice: before a worker
starts, and on every order.

## Graduation: RESEARCH -> PAPER -> LIVE_CONSERVATIVE (EM-189)

A strategy configuration does not go live because a flag says so. It is **promoted**, one stage at a
time, on evidence, and every step is appended to the `graduation_events` collection (monotonic `seq`
per strategy, unique index, never edited): who, when, from where to where, and the evidence cited.
The stage is bound to the config's behaviour hash, so editing the config puts it back to `research`
(retirement is the strategy's and survives edits). `production` exists in the vocabulary and is
**not promotable** in this release.

```
emporos graduation status [strategy]          # stage + which requirements of the next stage are met
emporos graduation promote <strategy> --to paper|live_conservative [--experiment EXP-...]
emporos graduation acknowledge <strategy>     # CLI only, needs a terminal
emporos graduation demote <strategy> --to <stage> --reason "..."     # always allowed, always recorded
emporos graduation history <strategy>
```

Requirements (one class each in `graduation/requirements.py`; every one **fails closed**, and a
promotion prints every unmet one together):

| stage | requirement | evidence |
|---|---|---|
| PAPER | `ValidatedVerdictForConfig` | latest verdict is VALIDATED for this behaviour hash (all robustness gates) |
| PAPER | `HoldoutEvaluated` | the cited EM-188 report reserved a holdout and has no holdout reason outstanding |
| PAPER | `DataIntegrityClean` | the report recorded its data provenance, assumed no un-allowlisted instrument, and ran under the quarantine in force now |
| LIVE | all of PAPER's, plus | |
| LIVE | `PaperReconciliationPassed` | newest cumulative EM-185 parity report is VALIDATED with at least `min_sessions` paper sessions |
| LIVE | `BrokerVerificationPassed` | every critical broker check is a PASS, not older than `broker_verification_max_age_days` (`config/graduation.yaml`) |
| LIVE | `LiveAcknowledged` | the typed acknowledgement exists for this configuration |
| LIVE | `NoOpenAnomalies` | kill switch clear, no UNKNOWN / PENDING_NEW order |
| LIVE | `JevDependencyAllowed` | only for a Jev-enabled deployment (`--jev-enabled`): the latest `jev_incremental` experiment for the config is ACCEPTED |

**Today every one of these refuses**, which is the correct state: no strategy is validated, no report
records its data provenance yet (`supporting.data_provenance` is not emitted by any report builder, so
`DataIntegrityClean` cannot pass), and **broker verification (EM-186) has produced no evidence**: its
seam (`BrokerVerificationEvidence`, `domain/broker_verification.py`) is bound to
`UnverifiedBrokerEvidence`, which reports every critical check as `unknown`. Nothing assumes a pass;
when EM-186 records real checks, only the binding in `cli/graduation_runtime.py` changes. Jev is not
wired into the live worker, so `--jev-enabled` must be given by whoever enables it for a deployment.

**The acknowledgement is deliberate.** `acknowledge` prints the risk tier, the capital, every limit
and the experiment and verdict behind the strategy, then requires `<strategy>@<hash8> LIVE` typed
exactly. It runs only on an interactive terminal, and it exists only on the CLI: the API has one
read-only route (`GET /strategies/{name}/graduation`) and no write route, consistent with the kill
switch not depending on the dashboard. Nothing in a script or a test may type it for you.

### The live risk tier (numbers approved by the operator on 2026-09-24)

Every order passes the risk engine, so conservative exposure is enforced there, not in a wrapper.
`config/risk.yaml` is aligned to the ₹50,000 production capital and the live worker loads
`config/risk.live_conservative.yaml`, which the loader refuses if any cap is looser than
`risk.yaml`'s. **These values began as the EM-189 plan's examples; the operator confirmed them on
2026-09-24. Changing one needs a fresh operator sign-off.**

| limit | `risk.yaml` (was) | `risk.yaml` | `risk.live_conservative.yaml` |
|---|---|---|---|
| `account_capital` | n/a | 50000 | 50000 |
| `max_capital_deployed` | 60000 | **50000** | **10000** (20%) |
| `max_daily_loss` | 2000 (4%) | **1000** (2%, the benchmark's) | **500** |
| `max_strategy_loss` | 1000 | 1000 | **300** |
| `max_position_value` | 25000 | 25000 | **5000** |
| `max_open_positions` | 3 | 3 | **2** |
| `max_risk_per_trade` | n/a | unset | **150** |

`max_risk_per_trade` is new: `MaxRiskPerTradeGuard` blocks an entry whose
`|limit - protective stop| x quantity` exceeds it, and blocks an entry with **no** protective stop
(unknown risk is unauditable). `Signal.protective_stop` is additive; every built-in strategy states
one (its own stop where it has one, else `risk.stop_loss_pct`). A manual dashboard entry carries no
stop, so under the live tier a manual entry is refused: exits and square-off are unaffected.

### Automatic shutdown on anomalies (the tripwire)

`session/tripwire.py` halts trading through the same kill switch on the first of: the market-data
feed down for `feed_drop_halt_seconds` inside the session; more than `stale_instrument_fraction` of
watched instruments stale; an order UNKNOWN for `unknown_order_seconds`; `rejection_burst_count`
broker rejections inside `rejection_burst_seconds`; the order-update feed down for
`order_feed_down_seconds` with orders open (thresholds under `tripwire:` in
`config/settings.base.yaml`). It runs in **paper and live** workers alike, alerts once, and never
auto-resumes: `emporos resume` clears it. **A tripwire halt blocks new orders but not exits**: the
kill switch records who set it, and `KillSwitchGuard` lets an EXIT through only when every set
switch was set by the tripwire, so square-off still flattens the book. An operator's own
`emporos halt` still blocks everything. (Reconciliation mismatch already halts through the
reconciler and is not duplicated.)

## The live composition (built, EM-142) — proved on the emulator, never on a real endpoint

`emporos worker live -s <strategy>` is the live worker, behind the same gate `run-live` checks:

1. **A seam in the worker composition.** `PaperWorkerComposer` and the new `LiveWorkerComposer`
   share one `_assemble` function for everything that does not depend on which broker is used; each
   supplies its own `BrokerSeam` (the broker, log-in and connection, the trading mode, the cash
   source, the durable flush and any extra jobs). The paper path is unchanged — the existing
   end-to-end and chaos suites pass untouched.
2. **A live venue** (`cli/live_venue.py`, `LiveVenue`). Logs in; connects the market-data socket
   **and the order-update socket** (`order_stream_client`); reports session and order-feed health.
   The risk layer's `BrokerHealthGuard` blocks every order until the order feed is reported up, so a
   venue that forgets the order socket trades nothing, safely.
3. **Credentials only where they belong.** The paper worker holds a market-data-only view of the
   broker. `LiveWorkerComposer` is the only composer ever handed an order-capable one, and only
   after the gate passes. The API and dashboard have neither.
4. **Proof on the emulator, not a real endpoint.** The chaos suite already drills `AngelOneBroker`
   over `FakeSmartApi` for lost and ambiguous replies (an order goes UNKNOWN and is resolved by
   looking it up by tag, never resent), restarts with open orders, and redelivered fills, at the
   execution-engine layer. `tests/e2e/test_live_worker.py` proves the composition itself on the same
   emulator: an ordinary session over `LiveWorkerComposer` places a real order at the emulator
   broker and fills it, and a second run proves `TradingModeGuard` blocks the same order when
   `LIVE_TRADING_ENABLED` is not true — the switch is load-bearing on the real composition root, not
   only in a rule's own unit test.

The order-safety invariants hold on the code the live worker reuses: limit orders only (`MARKET` and
`IOC` are not in the type system), intent written before the broker call, no blind retry, an
instrument with an UNKNOWN order is frozen, fills are idempotent, and every order passes risk. They
are proven on the paper and emulator brokers, not on the real one — nothing in this repository has
placed, or can place, a real order.

## Broker operational constraints (EM-186)

Scattered across test docstrings until now; centralized here.

* **One session per client code, account-wide.** Logging in a second time (a test, a second
  worker, a manual script) invalidates whatever session was already logged in as that client
  code — including a running worker's. Never run a live check against the same `.env` credentials
  a worker is using.
* **One login per second, account-wide** (`LOGIN_COOLDOWN_SECONDS` in `tests/integration/conftest.py`).
  Angel One enforces this with a plain-text 403 ("Access denied because of exceeding access
  rate"), not a JSON error or a 429 — `broker/angelone/limits.py`'s `EndpointGroup.LOGIN` cap and
  the retry/backoff stack are built around this.
* **Order endpoints are IP-gated; market data is not.** The dev API key has no static IP
  registered in the SmartAPI portal, so `placeOrder`/`cancelOrder`/etc. refuse it outright,
  regardless of every other flag — this is what makes paper trading (market data only) safe to run
  today and live trading structurally impossible until step 1 below is done. No MAC-based
  auth exists in Angel One's API; only the API key + TOTP + the registered IP for order endpoints.
* **Published per-endpoint rate limits** (`broker/angelone/limits.py`, marked `[VOLATILE]` —
  unverified against Angel One's current published table): login/account/order-book/position/
  search/holding at 1/s; `placeOrder` throttled to 5/s (below the 9/s the static-IP rollout
  allows, itself below the 20/s originally published) with 500/min and 1000/hour caps; quotes/LTP
  at 10/s, 500/min, 5000/hour; candles at 3/s, 180/min, 5000/hour.
* **The websocket layer is TLS-verified, our own transport** (not the SmartAPI SDK — AGENTS.md);
  reconnect/heartbeat behavior is proven live for a stable connection (`test_angelone_live_feed.py`)
  but **not yet proven live across an actual connection drop**, and no documented subscription-count
  ceiling exists yet for the market-data socket — both remain open items under EM-186.

### Live verified, 2026-09-23 (market open, ~12:47-12:52 IST), EM-186

`emporos worker run` (paper, every `config/strategies/*.yaml` loaded, none `--start`ed) run for
~5 minutes against the real Angel One feed and stopped with `SIGTERM` (clean exit, code 143 from
the signal): real-time 1m candles were constructed from live ticks and written correctly for every
subscribed instrument, with plausible non-zero volumes and IST-correct timestamps (spot-checked
directly against `candles`, e.g. `NSE:3499 1m 2026-09-23 07:18:00+00:00 vol=21297`). This is the
first time the live tick-to-candle pipeline has been exercised against real NSE data end-to-end
(EM-138's outstanding smoke test).

One finding: `market_data.no_ticks_at_open` (`marketdata/staleness.py`'s `_check_market_open`)
fired once at startup — a **false positive**, not a feed problem: the check fires when no tick has
been seen by `open + 1 minute` on the CURRENT session, and starting the worker mid-session (12:47,
not before 09:15 as the command's own docstring requires) means that window had already long
passed with nothing yet received in THIS run. Ticks began arriving and candles were written
correctly seconds later. Real: order-book/reconnect-under-loss/subscription-limit checks remain
unexercised (no orders can be placed — IP-gated — and this run was never disconnected mid-session).

## What you must do, in order

The composition exists now; none of the rest can be done by the codebase, and none of it has been
done.

1. **Register a static IP for the Angel One API key** in the SmartAPI portal, and run the worker
   from that address. Without it order endpoints refuse the key. Market data does not need it,
   which is why paper works today.
2. **Get a strategy validated.** Every verdict so far is `rejected`: about ₹3,100 lost per strategy
   at the ₹50,000 benchmark, with the 95% interval wholly below zero, no window profitable, and about
   0.37% per round trip in charges and buffer against no measurable edge. Validation also needs
   500 trading days of history and 150 out-of-sample trades. The history part can now be met: ten
   years of 5m bars are stored (`docs/data/history.md`); the trade count depends on the strategy.
   Read the data caveats first: unadjusted splits in three symbols, and survivorship.
3. **Paper trade it forward** on live data for long enough to trust it (`docs/paper-trading.md`).
   This has not been done for any strategy either.
4. **Set the flags yourself**, on the day, in this order: `enabled: true` in the strategy's config,
   commit it, confirm `emporos backtest verdicts list` still says `validated`, then
   `LIVE_TRADING_ENABLED=true`, then `emporos worker run-live -s <strategy>` must report nothing
   missing before `emporos worker live -s <strategy>` is run for real.
5. **Graduate it** (`emporos graduation status`, `promote --to paper`, then, once paper reconciliation
   and broker verification exist, `acknowledge` and `promote --to live_conservative`). The live risk
   numbers above are operator-approved (2026-09-24).
6. **Size the first day small.** Judge nothing from one session. Keep `emporos halt` and the SSM
   instance stop in reach: the kill switch does not depend on the dashboard.

## Compliance checklist (verify against the CURRENT rules before the first live order)

Design-level: this repository does not assume the rules, it lists what must be confirmed with the
current Angel One documentation and the current NSE/SEBI retail-algo circulars. Each item is a
critical check in `domain/broker_verification.py` (`CRITICAL_BROKER_CHECKS`), so it is a
machine-checked prerequisite rather than a promise in this document: it blocks
`promote --to live_conservative` until EM-186 records a recent PASS for it.

- [ ] **Order rate** stays below the exchange's threshold for unregistered algos (our budget is
  `max_orders_per_second: 2` in `config/risk.yaml`) - `order_rate_within_exchange_threshold`.
- [ ] **Static IP / API session constraints** confirmed, cross-referenced with EM-186 - `static_ip_registered`,
  `login_and_session`.
- [ ] **Algo-ID or order tagging** requirement confirmed with the broker; our `ordertag`
  idempotency tag must stay compatible - `algo_tagging_requirement_confirmed`, `ordertag_round_trip`.
- [ ] **Order lifecycle proven** on a real (tiny) limit order and the order-update socket -
  `limit_order_round_trip`, `order_update_socket`.
- [x] **Limit orders only**: `MARKET` and `IOC` are not in the type system.
- [x] **Kill switch reachable off-dashboard**: CLI, Mongo flag, file sentinel, SSM instance stop.
- [ ] **Audit retention**: `order_events` (monotonic seq), `risk_events`, `graduation_events` and
  `live_acknowledgements` are append-only and have no TTL; confirm the retention period the rules
  require is met by Atlas backups.

Nothing here enables live trading, and nothing in this repository has placed, or can place, a real
order — only ever the SmartAPI emulator.
