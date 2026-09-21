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
```

The conditions, each its own small class in `session/launch_gate.py`, all of which must hold:

| condition | what satisfies it | who does it |
|---|---|---|
| the switch | `LIVE_TRADING_ENABLED=true` in the environment | you |
| the strategy is enabled | `enabled: true` in `config/strategies/<name>.yaml` | you; a committed decision recorded in `docs/strategies/`, never a default |
| a verdict was recorded, and it is validated | `emporos backtest curate` concluded `validated` **for exactly this config** | the curation, not you |
| the verdict is not stale | the config's behaviour has not changed since it was judged | you, by not editing it (editing `enabled` is fine) |
| the kill switch is clear | not set, and readable | you |

There is no acknowledgement that gets past live: the typed-standing escape that paper allows does
not exist here. A live start with any condition failing is refused, with all the failing reasons
together.

Independently of this launch check, the risk engine's `TradingModeGuard` blocks every order in live
mode while `LIVE_TRADING_ENABLED` is not true, so the switch is checked twice: before a worker
starts, and on every order.

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
5. **Size the first day small.** Judge nothing from one session. Keep `emporos halt` and the SSM
   instance stop in reach: the kill switch does not depend on the dashboard.

Nothing here enables live trading, and nothing in this repository has placed, or can place, a real
order — only ever the SmartAPI emulator.
