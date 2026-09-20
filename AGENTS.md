# Emporos — Engineering Rules (CLAUDE.md)

You are working on **Emporos**, a Python 3.12 algorithmic trading platform for NSE/BSE
cash equity intraday via Angel One SmartAPI. This file is mandatory reading before any
code task. It binds you as tightly as the code review process would.

## Non-negotiable rules

1. **Every piece of code you write MUST follow Object-Oriented Programming (OOP) and
   SOLID.** This is not a style preference, it is a hard requirement, and it applies to
   prod code, tests, scripts, and tooling alike.
2. **Code must be easily scalable and human maintainable.** If you cannot explain the
   shape of an abstraction in one sentence, it is too complicated. Simpler code that
   composes is preferred over clever code that does not.
3. **Never downgrade the codebase's existing guarantees.** The project already enforces
   strict type checking, layered architecture, and import contracts. Your code must pass
   them. Do not weaken a contract to make a change easier.

## OOP + SOLID, applied to Python

- **Single Responsibility (S)** — One class, one job. A class is named after what it
  does: `OrderRouter`, `PositionLedger`, `RiskCheck`. If a class needs "and" in its
  description, split it. Keep methods short, focused, and side-effect-light.
- **Open/Closed (O)** — Extend behavior without editing existing, working code. If you
  are tempted to add a `if kind == "x"` branch inside a class, you are closing it for
  extension. Prefer strategy/policy injection, subclassing, or pluggable handlers.
- **Liskov (L)** — Subclasses must be safe drop-in replacements for their base. A
  subclass may narrow *inputs* or widen *outputs*, never the reverse. If a subclass has
  to raise or degrade behavior the base promises, it should not be a subclass.
- **Interface Segregation (I)** — No class should depend on methods it never calls.
  Favour small, focused interfaces (`Protocol` or ABCs with a handful of methods) over
  one fat base class. Split the fat interface; inject what each consumer actually needs.
- **Dependency Inversion (D)** — Depend on abstractions, not concretions. High-level
  modules (strategies, risk, execution) must not import concrete adapters
  (e.g. `emporos.broker.angelone`). Dependencies are injected in the constructor —
  never instantiated mid-method, never fetched from a god-object/module-level singleton.

## Python idioms that satisfy SOLID here

- **Protocols / ABCs for the "I" and "D" in SOLID.** Define the interface the consumer
  needs (`class OrderSender(Protocol)`) and inject implementations. Construct concrete
  adapters and wire the graph at the composition root (e.g. the CLI/bootstrapping layer),
  never inside domain or strategy code.
- **Small immutable value objects for domain data** — `dataclass(frozen=True)` or
  pydantic models for anything that crosses a boundary (events, orders, fills, positions).
  Represent state, not behaviour; keep behaviour in the classes that own the rules.
- **Composition over inheritance.** Prefer composing small collaborating objects over deep
  inheritance trees. Favour a few cooperating classes over one clever hierarchy.
- **Private state, public behaviour.** Hide internals behind `_`-prefixed attributes and
  expose a minimal public surface. Objects should be correct by construction (validate in
  `__init__`), never left half-initialized for callers to finish.
- **Testing follows the same rules.** Fake/poll/adapters implement the same `Protocol` as
  prod adapters. Tests should pass in lightweight doubles, not monkey-patch internals of
  the class-under-test, and never require real network/broker credentials unless marked
  `integration`.

## Architecture boundaries (plan.md §4, codified by import-linter)

- **`emporos.domain`** — pure. Imports nothing from `emporos` (no `core`, `broker`,
  `persistence`, `api`, `marketdata`, `execution`, `risk`, `strategies`). Domain is where
  SOLID is strictest.
- **`emporos.strategies`** — may import `domain` and `broker.base` only. NEVER imports
  `emporos.broker.angelone` or any concrete broker. A strategy never knows Angel One
  exists.
- **`emporos.api` / control layer** — must never import `emporos.broker`. Orders placed
  from the dashboard/CLI traverse the identical risk-and-execution path a strategy
  signal takes — never a shortcut around it.
- **Nothing outside `emporos.broker.angelone/`** may import SmartAPI concepts. Broker-
  neutral DTOs only; the adapter and `mapping.py` own all translation.
- Respect the layering: `core` < `domain` < `persistence`/`broker` < `strategies`/
  `execution`/`risk` < `api`/`cli`. New modules must slot into this ordering.

## Domain and systems invariants (from plan.md — read before writing code)

- **Limit orders only.** The `OrderType` enum contains `LIMIT` and `STOPLOSS_LIMIT` only.
  `MARKET` and `IOC` are absent from the type system — a forbidden order must be
  *unrepresentable*, not merely validated against. No code path may emit one.
- **Never blind-retry an order.** Order intent is written to Mongo (`PENDING_NEW`, unique
  index on `idempotency_key`) *before* the broker call. An ambiguous outcome (timeout,
  reset, 5xx) goes `UNKNOWN` and is resolved by querying the broker via `ordertag`
  (`find_orders_by_tag`) — never by resending. **Restart safety is the invariant:** no
  restart path may place a duplicate order.
- **Exactly one process reaches Angel One.** Only the worker holds broker credentials or
  opens a broker session. The API and dashboard have neither — a "square off everything"
  click and a strategy exit traverse identical code.
- **Risk gates every order.** Every live order passes through the risk engine, then
  execution. Risk rules are individual classes evaluated as a pure, ordered list over an
  immutable snapshot. Rejections are always persisted with full evaluation context — a
  silently dropped signal is unauditable.
- **Execution audit trail.** Every order-state transition is appended to `order_events`
  with a monotonic sequence number. Repricing is cancel-then-replace, never
  modify-in-place. Fill processing is idempotent via a unique index on `broker_trade_id`.
  An instrument with any `UNKNOWN` order is frozen for new orders.
- **Ordering matters in trading.** In-process dispatch is synchronous and total-ordered
  (a fill applies before the next signal evaluates). A strategy handler that raises is
  isolated — it halts that strategy and alerts, but must never kill the session.
- **Hide storage behind the repository.** All candle access goes through
  `CandleRepository`; the hot/cold (Mongo/S3) split is an implementation detail, and no
  caller queries the `candles` collection directly. Mongo connection URI, write concern,
  and pool sizes are configuration, not literals.
- **Own transport, TLS verified.** We implement our own REST/WS transport with SSL
  verification enabled. The SmartAPI SDK is a **dev-only** contract-test oracle, never
  in the money path.
- **Kill switch independence.** The kill switch must not depend on the dashboard — it
  stays reachable via CLI, a Mongo flag, a local file sentinel, and SSM instance stop.

## Testing rules (plan.md §18)

- **Integration tests use real MongoDB** via `MONGO_URL` against the Atlas `emporos_dev`
  database — never `mongomock`. It diverges exactly where we depend on real semantics
  (unique indexes, time-series restrictions).
- Every `Broker` implementation passes the **same shared contract suite** (recorded
  SmartAPI fixtures + the pinned SDK as an oracle).
- Backtest changes are **golden-file regression** — any metric drift fails the build.
- Fault-injection fakes for the failure suite: duplicate placement, ambiguous response →
  `UNKNOWN`, WS drop mid-order, redelivered fills, restart with open orders.
- **Coverage gates:** `risk/`, `execution/`, `domain/` ≥ 90%; elsewhere ≥ 70%. The gate
  is high precisely where a bug costs money.

## Work tracking and commits (plan.md §21)

- **All work is tracked in Jira, project `EM`. No code is written for untracked work.**
  File the issue in **To Do** before starting, move to **In Progress** when work begins,
  and move to **Done** only once the change is **committed**.
- **Every commit references the issue key** with no trailers and no tool attribution:

  ```
  EM-123: add idempotency key unique index to orders
  ```

- **If the Atlassian MCP is unavailable, stop and ask the operator to reconnect it**
  before doing anything else. Do not proceed with untracked work on a local
  substitute backlog.

## Definition of done — always run before finishing

```bash
pipenv run lint            # ruff check src tests
pipenv run typecheck       # mypy --strict on the whole package
pipenv run test            # pytest suite, writes .coverage
pipenv run coverage-gate   # plan.md §18: domain/risk/execution ≥90%, elsewhere ≥70%
pipenv run lint-imports    # import-linter architecture contracts
```

All five must pass. Never claim a task is complete until they do.

## If there is a conflict

When a pragmatic shortcut conflicts with these rules (or with plan.md), the rules win —
but flag it to the user in your final summary, with the exact trade-off and a concrete
refactor to do it properly.