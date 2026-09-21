# Phases 12–15 (and observability): what exists, what does not

Tracked in Jira: EM-38 (execution), EM-39 (portfolio and reconciliation), EM-40 (control plane),
EM-109 (worker), EM-110 (observability), EM-111 (strategy curation). **EM-41, the dashboard, is
deliberately not started.**

## What is built and how it was verified

| Area | What it does | Verified by |
|---|---|---|
| Execution engine | Writes the intent first, one broker call, never retries an ambiguous placement; resolves doubt through `find_orders_by_tag`; an order is only called absent after several checks over a window (persisted, restart-safe) | Unit suite; chaos suite over the in-memory, AngelOne-emulator and paper brokers with lost replies, crashes, redelivered fills and seeded random sessions (a mutant that blind-retries fails 21 of its 30 cases) |
| Fills | Idempotent on `broker_trade_id`; execution + order + audit event + position in one transaction; the only place an order's fill quantity moves | Unit tests; real-Atlas journal tests (redelivery, lost race, cross-account) |
| Repricing | Cancel, confirm (applying any unapplied fills first), fresh risk review, replace; bounded count and chase; works from the persisted order, so restart-safe | Unit tests incl. chain termination and traceability |
| Portfolio / reconciliation | Marks, unrealised P&L (or an honest "unknown"), snapshots reconstructable from fills, reconciler that adopts only what fill adoption explains and halts through the kill switch otherwise | Unit tests per discrepancy kind; real paper session on Atlas: clean run, restart, corrupted position, manual broker order, deleted execution, unapplied fills |
| Worker | One process, deterministic poll loop: recover → reconcile → trade → square off → end-of-day; HALTED until an operator resumes | Real-Atlas e2e in virtual time: ordinary session, process death mid-session, mid-session discrepancy, dirty start + resume |
| Control plane | Commands recorded once (unique client key), claimed atomically, executed exactly once, resumed after restart, expired with a reason; all 12 catalogue commands; manual orders pass every risk rule a signal does | Unit tests; real-Atlas e2e where an operator drives every command over HTTP while a session runs |
| API | Typed read endpoints, 202 command submit, Argon2id passcode + HS256 JWT (algorithm pinned, throttled login), resumable SSE, pinned OpenAPI | Real-Atlas HTTP tests; fresh-interpreter test that the API loads no broker code |
| Observability | Alerts and state changes persisted as system events; eight EMF metrics; loopback health report | Unit tests + a real session |

## Deviations and decisions to confirm

- **The paper broker keeps its own books** (`paper_*` collections); the platform's order state has
  exactly one writer. Run `emporos db migrate` in each environment.
- **`modify_order` is not exposed to execution** (EM-99 F5): repricing is cancel-then-replace only.
- **Risk in backtests uses synthetic facts** where a replay cannot know them (spread = 0, a 20%
  circuit band); stated in `backtest/risk_gate.py`.
- **Order-safety guard narrowed** to endpoint names: a command type called `CANCEL_ORDER` is not an
  endpoint.
- **momentum_v1 is now disabled** (a year of real data loses money).

## Still open (needs a live session, credentials or a decision)

1. **The paper worker has never run on live NSE ticks.** `emporos worker run` (EM-138) wires it to
   the live tick pipeline and is tested with a scripted feed; the open-market smoke test is
   outstanding (EM-99 G1/H14).
2. **Nothing has touched a real broker order endpoint** (EM-99 D1/F1): the AngelOne path is verified
   only against a SmartAPI emulator written from the documentation.
3. **The dashboard (EM-41)** — not started, by instruction.
4. **EM-99 items that need a live session or S3:** A1–A3, B1–B9, C1–C3, C7–C8, D1–D6.
5. **Not done, code-only:** A5 (partial bars are not re-fetched by gap reconciliation), C4 (nothing
   schedules the nightly rollup), C5 (no daily-bar backfill), H7 (the session's last 1h bar).
6. **Backfill from the command bus** has no runner in a paper worker (the command is REJECTED with
   that reason).
7. Live-mode composition (real broker, `TradingMode.LIVE`) does not exist yet; paper is the only mode.
   The gate in front of it is built (EM-140: `emporos worker run-live`, `session/launch_gate.py`) and
   refuses by default; the composition is EM-142. See `docs/live-trading.md`.
8. The dashboard now shows each strategy's recorded verdict and its running status, and starts a
   strategy that is not validated only with its standing typed (EM-139).

See `docs/strategies/` for the strategy curation result and its caveats.
