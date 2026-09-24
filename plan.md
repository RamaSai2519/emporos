# Emporos — Algorithmic Trading Platform: Architecture & Implementation Plan

> Status: planning complete, no code written yet.
> Research date: 2026-09-19. Sections marked **[VOLATILE]** depend on broker/regulatory facts that change; re-verify before implementation.

---

## Context

We are building a personal, production-grade algorithmic trading platform ("Emporos") from zero, trading NSE/BSE **cash equity intraday** through **Angel One SmartAPI**, in **Python** with **Pipenv**, persisting to **MongoDB Atlas**, deployed on **AWS**, with **no Redis**.

Confirmed scope decisions:

| Question | Answer | Consequence |
|---|---|---|
| Instruments | Equity cash + intraday only | Small instrument master, one WebSocket connection suffices, no expiry/Greeks/rollover logic in v1 |
| Budget | Rock bottom, ~$10–25/mo total | Single small EC2 + Elastic IP; Atlas M0 (dev) / Flex (prod); no NAT Gateway; no continuously-running staging |
| Broker status | Trading account exists, no API access yet *(at planning time; since then: SmartAPI onboarded, and the Elastic IP `65.0.238.146` registered as the API key's static IP on 2026-09-25, EM-211)* | Roadmap must include SmartAPI onboarding, TOTP enrolment, static-IP app creation |
| Tenancy | Single user, single Angel One account | `users`/`accounts` exist but degenerate; no multi-tenant auth; read-only dashboard later |

The goal of this document is that a coding agent can be handed "Implement Phase N" and know exactly what to build, where, why, what interfaces it exposes, what tests are required, and what completion means.

---

## 1. External constraints (researched)

These facts drive the architecture. Each is cited; each is marked for re-verification where it is likely to drift.

### 1.1 Regulatory — SEBI / NSE retail algo framework **[VOLATILE]**

The framework became mandatory for all Indian stockbrokers on **1 April 2026**, following SEBI's Feb 2025 circular and NSE's implementation standards.

- **Every order placed via API is an algo order.** Tagging and compliance apply even below the OPS threshold.
- **Every algo order carries a unique exchange-assigned Algo ID.** Below **10 OPS per exchange/segment**, strategies use a generic exchange-provided ID; above it, individual registration with each exchange is required. We will stay well below 10 OPS by design.
- **Static IP is mandatory** for API order placement — a registered, whitelisted IPv4.
- **Market orders and IOC orders are prohibited** for algorithmic trading, across equity and commodity segments.
- **Kill switch is required.**
- Retail algos must be hosted on broker servers, **except** "tech-savvy" clients who keep their own logic and register a static IP. That exemption is the category we operate under, and it also exempts us from mandatory monthly mock trading sessions.

> Implication: this is not merely a deployment detail. It forbids market orders in the execution engine, caps order rate, and requires a fixed egress IP. All three are designed in from Phase 1, not retrofitted.

### 1.2 Angel One SmartAPI — authentication & session

- Base REST URL: `https://apiconnect.angelone.in`
- Login: `/rest/auth/angelbroking/user/v1/loginByPassword` with client code, PIN, and TOTP.
- Returns `jwtToken`, `refreshToken`, `feedToken`.
- Token refresh: `/rest/auth/angelbroking/jwt/v1/generateTokens`
- **Sessions are force-logged-out daily at midnight.** Re-login every trading day is mandatory, not optional.
- TOTP is a 30-second rotating code from an enrolled authenticator secret (generate with `pyotp`).
- Required headers: `X-PrivateKey` (API key), `X-ClientLocalIP`, `X-ClientPublicIP`, `X-MACAddress`, `X-UserType: USER`, `X-SourceID: WEB`, `Authorization: Bearer <jwt>`.

### 1.3 Angel One SmartAPI — static IP flow **[VOLATILE]**

- Static-IP API keys are created through a **separate "New Login"** at `smartapi.angelone.in` — legacy keys are managed under the old login and are being phased out.
- Up to **five static IPs per API key**; a primary plus a secondary for redundancy.
- **IP changes limited to once per calendar week.** This is a hard operational constraint: we cannot casually re-provision the worker's IP.
- IPv4 only; IPv6 "in the near future".
- A static IP maps to one client (family sharing requires documentation and was listed as not yet enabled).
- **Static IP registration is optional for non-order APIs** — only order placement is IP-gated.

> Implication: the Elastic IP is a long-lived, registered asset. Terraform must treat it as protected (`prevent_destroy`), and disaster recovery cannot assume we can swap IPs freely.

### 1.4 Angel One SmartAPI — rate limits **[VOLATILE]**

Per-minute caps are **additional to** per-second caps, not derived from them.

| Endpoint | /sec | /min | /hour |
|---|---|---|---|
| `loginByPassword` | 1 | — | — |
| `placeOrder` | 20 (documented) | 500 | 1000 |
| `getOrderBook` | 1 | — | — |
| `getLtpData` | 10 | 500 | 5000 |
| `getPosition` | 1 | — | — |
| `searchScrip` | 1 | — | — |
| `getHolding` / `getAllHolding` | 1 | — | — |
| `market/v1/quote` | 10 | 500 | 5000 |
| `getCandleData` | 3 | 180 | 5000 |

Two caveats found in the forum:

1. The static-IP rollout post states order rate was **reduced to 9 orders/second** for both old and new keys, contradicting the documented 20/sec. **We design for ≤5 OPS** and treat 9 as the ceiling.
2. **Known unresolved defect:** `getCandleData` and `getOrderBook` return `"Access denied because of exceeding access rate"` at rates as low as ~0.003 req/sec — roughly 900× below the published limit — reported by at least six independent users across consecutive sessions, including on the first request of a fresh session. Officially unacknowledged. The only working mitigation is client-side retry with exponential backoff.

> Implication: the historical ingestion pipeline must be built around the assumption that rate-limit errors are **spurious and frequent**, not a signal that we are actually too fast. Backoff, resumability, and gap-detection are core requirements, not polish.

### 1.5 Angel One SmartAPI — market data WebSocket (SmartWebSocketV2)

- URL: `wss://smartapisocket.angelone.in/smart-stream`
- Auth via `Authorization`, `x-api-key`, `x-client-code`, `x-feed-token` headers.
- Modes: `LTP` (1), `QUOTE` (2), `SNAP_QUOTE` (3), `DEPTH` (4).
- Exchange types: NSE_CM (1), NSE_FO (2), BSE_CM (3), BSE_FO (4), MCX_FO (5), NCX_FO (7), CDE_FO (13).
- **Max 3 concurrent connections per client code.**
- **Max 1000 tokens subscribed per connection.**
- DEPTH mode: max 50 tokens per request, NSE_CM only.
- Heartbeat: client sends `ping` every ~10s, expects `pong`; last-pong timestamp used for liveness.
- Binary payloads, little-endian, with subscription mode, exchange type, token, sequence number, timestamps and prices.

### 1.6 Angel One SmartAPI — order updates

- Order status WebSocket: `wss://tns.angelone.in/smart-order-update`, `Authorization: Bearer <jwt>`, **3 connections per client code**.
- Payload includes `user-id`, `status-code`, `order-status`, `error-message`, and `orderData` containing `variety`, `ordertype`, **`ordertag`**, `producttype`, `price`, `triggerprice`, `quantity`.
- Postback/webhook URLs exist (configured at API key creation) but only cover orders placed through that API key — not orders placed from the Angel One app/web.

> **`ordertag` is echoed back on both the order book and the order-update stream.** This is the single most important fact for idempotency: it gives us a client-controlled correlation handle that survives a lost HTTP response. The entire duplicate-order-prevention design rests on it.

### 1.7 Angel One SmartAPI — instrument master

- Full tradable instrument list: `https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json`
- Unauthenticated plain HTTPS JSON. Fields: `token`, `symbol`, `name`, `expiry`, `strike`, `lotsize`, `instrumenttype`, `exch_seg`, `tick_size`.
- Published as a daily-refreshed file (no documented publication SLA — treat as best-effort and validate).

### 1.8 Official Python SDK status

- `smartapi-python`, latest **1.5.5, released 2025-02-07**. 53 releases since Oct 2020.
- Classified **inactive** — no PyPI releases in over 12 months, no recent PR or issue activity. Snyk reports no known CVEs.
- Source inspection findings:
  - **No retry logic, no backoff, no rate limiting anywhere** in `smartConnect.py`.
  - `smartWebSocketV2.py` connects with **SSL certificate verification disabled**.
  - Default `max_retry_attempt` is 1.

> Implication: see Decision 4. We do not put an unmaintained library with disabled TLS verification and no rate limiting in the money path.

### 1.9 AWS & MongoDB Atlas cost facts **[VOLATILE]**

- NAT Gateway, ap-south-1: **$0.045/hr (~$32.40/mo) + $0.045/GB processed**, plus standard $0.09/GB egress.
- Public IPv4 address: **$0.005/hr (~$3.65/mo)** per address since 1 Feb 2024 — charged whether attached or idle.
- EC2 `t4g.small` on-demand is roughly $0.013–0.017/hr depending on region; ap-south-1 sits at the lower end (~$10/mo). Verify against the AWS calculator at implementation time.
- MongoDB Atlas: **M0 free** (512 MB storage, shared), **Flex $8–30/mo** (~5 GB, hard monthly cap), **M10 dedicated from ~$57/mo**.
- **Atlas Data API and custom HTTPS endpoints were removed on 30 September 2025.** Any design that assumed HTTP access to Atlas without a driver is dead. We use the native driver exclusively.
- Atlas PrivateLink / VPC peering require M10+; they are unavailable on M0/Flex and therefore out of scope at this budget.

---

## 2. Executive architecture recommendation

**Deploy a single `t4g.small` EC2 instance in ap-south-1 with a registered Elastic IP, running the trading worker, a control API, and a static Next.js dashboard as containers under systemd, talking to MongoDB Atlas, with S3 for historical data and CloudWatch for alarms. Use zero Lambda in v1.**

```
        Next.js dashboard on VERCEL  (deployed by operator)
                    │  Auth.js / Google OAuth, single-email allowlist
                    │  signed JWT in Authorization header
                    ▼
        https://empapi.journeymen.in   (A record → Elastic IP)
                    │  TLS via Caddy + Let's Encrypt (TLS-ALPN-01)
                    │  CORS: production dashboard origin only
                              Angel One SmartAPI         │
                                     ▲                   │
              REST (orders, history) │ WSS (data/orders) │
                                     │                   │
                         ┌───────────┴────────────┐      │
                         │   Elastic IP (static)  │      │
                         └───────────┬────────────┘      │
                                     │                   │
   ┌─────────────────────────────────┴───────────────────┴──────────────┐
   │  EC2 t4g.small · ap-south-1 · public subnet · inbound 443 ONLY     │
   │                                                                    │
   │   Caddy (TLS, rate limit) ──▶ FastAPI control API (localhost bind) │
   │   [cpu/mem capped — must never starve the worker]                  │
   │                                    │          │                    │
   │                          reads (RO)│          │writes COMMANDS     │
   │                                    │          │  (never the broker)│
   │  ┌─────────────────────────────────┼──────────┼──────────────────┐ │
   │  │              emporos-worker     │          ▼                  │ │
   │  │                                 │   Command Processor         │ │
   │  │  Session Orchestrator           │          │                  │ │
   │  │         │                       │          ▼                  │ │
   │  │  MarketData ─▶ Normalizer ─▶ Aggregator ─▶ Strategy Engine    │ │
   │  │         │                                  │                  │ │
   │  │         │                    Signal ◀──────┘                  │ │
   │  │         │                       ▼                             │ │
   │  │         │                  Risk Engine  ◀── commands land here│ │
   │  │         │                       ▼           too — same path   │ │
   │  │         │                Execution Engine                     │ │
   │  │         │                       ▼                             │ │
   │  │         │             Broker (AngelOne | Paper)               │ │
   │  │         ▼                       ▼                             │ │
   │  │    Persistence ◀── Reconciler ◀── Order Updates WS            │ │
   │  └───────────────────────────────────────────────────────────────┘ │
   └────────────┬──────────────────────────────┬────────────────────────┘
                │                              │
                ▼                              ▼
         MongoDB Atlas                   S3 (history, backups,
    hot state + hot candles               cold candles, archives)
      + `commands` bus                          │
                                                ▼
                                     CloudWatch metrics/alarms → SNS → email
```

**The load-bearing invariant:** the dashboard and API have **no broker credentials and no broker code**. A "square off everything" click becomes a command document, which the worker executes through the identical risk and execution path a strategy signal takes. There is exactly one process that can reach Angel One.

**The accepted trade:** publishing the API means the trading box has a public listener, so app-layer auth is the entire perimeter (Vercel's dynamic egress makes IP filtering impossible). Caddy and the API are resource-capped so that abuse of the public endpoint degrades the dashboard but **cannot** degrade trading.

**v1 contains no Lambda, no NAT Gateway, no API Gateway, no ALB, no Redis, no Kafka, no Kubernetes, no message broker.** The command bus is a MongoDB collection, because MongoDB is already there and gives ordering, durability, status and query in one place.

---

## 3. Major architecture decisions

Each decision states reason, alternatives, why rejected, operational implications, and cost.

### Decision 1 — Single EC2 instance with Elastic IP as the trading worker

**Decision.** One `t4g.small` (2 vCPU burstable, 2 GB RAM, ARM64) in a public subnet with an Elastic IP, running the worker under Docker + systemd with `Restart=always`.

**Reason.** Two constraints collide and admit exactly one cheap answer. Orders must originate from a registered static IPv4 (§1.3), and the market-data feed is a long-lived WebSocket with a 10-second heartbeat (§1.5). EC2 with an Elastic IP satisfies both natively: the EIP *is* the static egress IP, and a normal process holds a socket indefinitely.

**Alternatives considered and rejected.**

| Alternative | Why rejected |
|---|---|
| **Lambda** | Cannot hold the WebSocket at all — see Decision 2. Also needs VPC + NAT for a static egress IP, at 10× the compute cost. |
| **ECS/Fargate** | A Fargate task's public IP is assigned per-task and changes on every restart, so it cannot be the registered static IP. Achieving static egress requires a private subnet + NAT Gateway: **+$32.40/mo before any data processing**, against a worker that costs ~$10/mo. Paying 3× the workload cost for the network is indefensible at this budget. |
| **App Runner** | Same static-egress problem (needs VPC connector + NAT), and AWS is sunsetting the service — building on a deprecating platform is unjustifiable. |
| **EKS / Kubernetes** | Control plane alone is $72/mo, more than the entire target budget, to orchestrate one container. |
| **Two EC2 instances (HA)** | Doubles cost and the EIP count, and Angel One permits only one active session per client code, so a hot standby cannot actually trade concurrently. A stopped standby AMI is the correct cheap answer instead. |

**Operational implications.** Single point of failure by design. Mitigated by systemd restart, a crash-safe recovery protocol (§13), a CloudWatch dead-man's-switch alarm, and a documented ~15-minute rebuild from AMI + Terraform. The EIP must be treated as a protected resource: Angel One permits IP changes only once per calendar week, so a careless `terraform destroy` could lock us out of live trading for days.

**Cost.** ~$10/mo compute + $3.65/mo IPv4 + ~$2/mo EBS ≈ **$15.65/mo**, versus ~$45+/mo for the cheapest Fargate equivalent.

### Decision 2 — A persistent WebSocket cannot live in Lambda

**Decision.** The market-data and order-update WebSockets live in the long-running worker process. Lambda is never used for broker connectivity.

**Reason.** This was explicitly investigated as the pivotal question. Lambda's execution model is incompatible with the feed on four independent grounds, any one of which is disqualifying:

1. **Lifetime.** Maximum invocation is 15 minutes; an Indian equity session runs 6h15m (09:15–15:30 IST). The connection would be torn down and re-established at least 25 times per session, each requiring re-subscription of the full token list.
2. **Freezing.** Between invocations Lambda freezes the execution environment. Background threads stop. The 10-second heartbeat would not be sent, and the server would drop the connection. Critically, ticks arriving during a freeze are silently lost — no error, no backpressure, just a gap.
3. **No inbound delivery.** Lambda is invoked by events; it cannot be the *recipient* of a server-initiated push on a socket it opened. There is no mechanism for an inbound WebSocket frame to wake a frozen environment.
4. **State.** Candle aggregation and strategy state are inherently stateful across ticks. With Redis forbidden and every invocation needing to reload state from MongoDB, per-tick database round-trips would dominate latency and cost far more than the EC2 instance.

API Gateway WebSocket APIs do not help: they let clients connect *to us*, whereas we need to connect *out to* the broker.

**Alternatives considered.** EventBridge-triggered Lambda every minute to poll `getLtpData` instead of streaming: rejected because the quote endpoint is capped at 10/sec and 500/min, giving far coarser data than the tick stream, while costing more in invocations than the EC2 instance and still failing to provide order-update latency. A Step Functions loop holding the connection: rejected — Step Functions orchestrates Lambdas, inheriting every limitation above.

**Operational implications.** All broker connectivity is concentrated in one process, which simplifies session management (one session per client code is all Angel One permits anyway) and makes reconnect logic testable in one place.

**Cost.** Avoids both NAT Gateway (~$32/mo) and per-invocation charges.

### Decision 3 — Zero Lambda in v1; only the worker holds broker credentials

**Decision.** No Lambda functions are deployed in v1. Scheduled work (instrument refresh, EOD reports, backups, rollups) runs inside the worker process on an in-process scheduler. **Only the EC2 worker holds broker credentials or opens a broker session.**

**Reason.** The brief asked to use Lambda where it genuinely minimises cost. Honest analysis says: nowhere, here. Every candidate job is either (a) broker-authenticated — and adding a second session risks conflicting with the worker's single permitted session — or (b) small enough that running it in the already-running worker costs literally nothing extra. Adding Lambda would mean either Atlas allowlisting `0.0.0.0/0` (Atlas M0/Flex cannot do PrivateLink) or putting Lambda in a VPC with a NAT Gateway at $32/mo. Both are worse than a `while True` loop on a box we are already paying for.

The "only the worker touches the broker" rule is worth more than the Lambda savings: it means there is exactly one place where credentials live, one session lifecycle, one rate limiter, and one audit path. It structurally enforces the principle in §41 of the brief — nothing can reach Angel One except through risk and execution.

**Alternatives considered.** Lambda for EOD reports (rejected: needs Atlas access, saves nothing over an in-process job); Lambda as an out-of-band kill switch (rejected: a CloudWatch alarm plus a Mongo-flag poll plus SSM `stop-instance` achieves the same with no code); EventBridge Scheduler instead of in-process cron (rejected: adds a service to trigger something in a process that is already running).

**Operational implications.** Scheduled jobs die with the worker — acceptable because if the worker is down, trading is down and the alarm has already fired. Revisit at Phase 19 if the dashboard must serve data while the worker is stopped.

**Cost.** $0. Lambda free tier would have covered the invocations, but the NAT Gateway required to reach Atlas from a VPC-attached Lambda would not have been free.

### Decision 4 — Own HTTP and WebSocket transport; SDK pinned as a test oracle only

**Decision.** Implement our own REST client (`httpx`) and WebSocket client (`websockets`) against the documented SmartAPI endpoints. Keep `smartapi-python==1.5.5` as a **dev-only** dependency, used in contract tests to cross-check our request shapes and binary parsing.

**Reason.** The SDK is unmaintained (no release since Feb 2025, flagged inactive), has **no retry, backoff, or rate limiting**, and **disables SSL certificate verification** on the WebSocket. We need rate limiting (§1.4), aggressive backoff for the spurious `getCandleData` rate-limit defect, idempotency-aware error classification, and structured typed errors regardless. Retrofitting all of that around a synchronous, unmaintained client is strictly more work than writing ~15 endpoint calls — and it would leave a library with disabled TLS verification sitting in the path that places real orders.

**Alternatives considered.** Use the SDK directly behind the adapter (rejected: inherits every defect above into the money path, and pins us to a library that may never be updated for API changes). Vendor and patch the SDK source (rejected: we own a fork's maintenance burden without owning its design). Wait and swap later at Phase 17 (rejected: the transport is the foundation for rate limiting and idempotency, so retrofitting it after the execution engine is built means rewriting the execution engine's error handling).

**Operational implications.** We own transport correctness, which means contract tests against recorded fixtures become mandatory (Phase 4). The upside is that a SmartAPI change is a small, localised fix rather than waiting on an inactive upstream.

**Cost.** Zero direct cost; roughly two extra days in Phase 4, repaid immediately in Phase 12.

### Decision 5 — MongoDB Atlas M0 now; hosting decision deliberately deferred to Phase 17

**Decision.** Run on Atlas **M0 (free)** through Phase 18. At Phase 19, choose between **Atlas Flex** and **self-hosted MongoDB on the EC2 instance** using real measured data volumes. Until then, **all candle access goes through a `CandleRepository` interface** so that either outcome is a configuration change rather than a rewrite.

**Reason.** The three options are genuinely close, and the deciding evidence does not exist yet.

| Option | EC2 | EBS + snapshots | Atlas | **Total** |
|---|---|---|---|---|
| Atlas Flex + t4g.small | 9.80 | 1.92 (20 GB) | 10.00 | **21.72** |
| **Self-host on t4g.small, 60 GB** | 9.80 | 6.47 | 0 | **16.27** |
| Self-host on t4g.medium, 60 GB | 19.60 | 6.47 | 0 | **26.07** |

Self-hosting saves roughly **$5.50/mo** — but only if 2 GB of RAM holds, and the Next.js control plane (Decisions 12–13) now shares that box, which tightens the margin further. Size up to 4 GB for comfort and self-hosting becomes *more expensive* than Atlas. And through Phases 1–18 we are on M0, which is free, so self-hosting saves **nothing at all** during the entire build. There is no cost argument for deciding now, and deciding now means deciding without data.

The stronger argument for self-hosting is architectural, not financial: the only reason to tier candles to S3 is Atlas's ~5 GB Flex cap. The actual volume is small — 200 instruments × 375 bars × 250 sessions ≈ 18.75M documents ≈ 2.25 GB/year, which on EBS is about **$0.20/month**. With a local database, all history stays hot, and the nightly rollup job and hot/cold union loader — both correctness-critical, both needing their own tests — simply do not need to exist.

The argument against is failure-domain coupling, and it lands on the most dangerous possible target: the crash-recovery protocol (§13) depends on order state surviving the worker's death, since that is how `UNKNOWN` orders are resolved without placing duplicates. Co-locating the database means one volume or instance loss takes out both the worker and the record of what it was doing. Two secondary risks: `t4g` instances are **burstable**, so a mongod compaction or index build during market hours competes for CPU credits with the trading loop; and on 2 GB the OOM killer could take the worker.

**Alternatives considered.** Atlas M10 (rejected: $57/mo, over four times the budget ceiling, to store append-only data that needs no dedicated cluster). Everything in S3 including live state (rejected: no transactions, no unique indexes, no query — orders and positions genuinely need a database). DynamoDB for hot state (rejected: a second database for no benefit). Atlas Online Archive (rejected: M10+ only). Committing to self-hosting now (rejected: saves $0 during the build and forecloses a decision that costs nothing to defer).

**Operational implications.** The deferral has a price: `CandleRepository` must be written so the hot/cold split is an implementation detail, and the S3 tiering must be built anyway (M0's 512 MB makes it mandatory during the build regardless). If Phase 19 chooses self-hosting, that machinery becomes optional rather than wasted — it is still the right design for feeding backtests from cheap storage. Phase 17's Terraform provisions the data volume as a **separate EBS volume with `delete_on_termination = false`** either way, so adopting self-hosting later needs no re-architecture.

A self-hosted mongod **must be initialised as a single-node replica set** — both our fill transactions and the dashboard's change streams (§15.2) require an oplog. A standalone `mongod` would silently break both.

**Cost.** $0 through Phase 18; $16–22/mo thereafter depending on the Phase 19 outcome.

**Phase 19 decision criteria** — choose self-hosting if *all* hold: measured RSS for worker + API + Caddy + cloudflared under load leaves ≥ 1 GB headroom on t4g.small; CPU credit balance stays healthy across a full paper session; nightly `mongodump` → S3 and a **tested restore** are in place; the EBS data volume is on daily DLM snapshots. Otherwise take Flex and keep the failure domains separate.

### Decision 6 — Candles in regular collections, not time-series collections

**Decision.** `candles` is a **regular collection** with a unique compound index on `{instrument_id, timeframe, ts}`. Time-series collections are used only for optional raw tick archival.

**Reason.** Candle ingestion must be idempotent — a reconnect, a backfill re-run, or a retried historical fetch must not create duplicate bars. Idempotent ingestion requires an upsert against a unique key. **MongoDB time-series collections do not support unique indexes**, which makes idempotent upsert impossible on them. This is a case where the purpose-built feature is the wrong choice, and the reason is easy to miss until duplicate bars silently corrupt a backtest.

**Alternatives considered.** Time-series collections with application-level dedupe (rejected: a dedupe check-then-write is racy and gives exactly the silent corruption we are trying to prevent). Time-series plus a separate uniqueness-tracking collection (rejected: two writes that can diverge, reinventing a unique index badly).

**Operational implications.** We forgo time-series bucketing compression on candles, costing some storage — a trade already absorbed by tiering cold data to S3 (Decision 5).

**Cost.** Marginally more Mongo storage; immaterial at 90-day retention.

### Decision 7 — Idempotency via `ordertag` correlation, never blind retry

**Decision.** Every order intent is assigned a client-generated idempotency key and a short correlation tag, **persisted to MongoDB before the broker call is made**. The tag travels in SmartAPI's `ordertag` field. On any ambiguous outcome (timeout, connection reset, 5xx), the order is marked `UNKNOWN` and resolved by **querying the order book for that tag** — never by resending.

**Reason.** This is the highest-consequence correctness problem in the system: a duplicate order is real money lost. HTTP gives no way to distinguish "the broker never received it" from "the broker accepted it and the response was lost". The only safe resolution is an idempotency handle the broker echoes back, and §1.6 establishes that `ordertag` is echoed on both the order book and the order-update stream. That makes it the correlation key. Writing intent before acting means a crash between intent and response is always recoverable, because recovery can ask "does an order with this tag exist at the broker?"

**Alternatives considered.** Trusting a broker-side idempotency key (rejected: SmartAPI does not document one). Matching on symbol + quantity + price + timestamp (rejected: heuristic, and ambiguous precisely when two legitimate identical orders are intended). Retrying on timeout with a short window (rejected: this is the exact mechanism by which duplicate orders happen).

**Operational implications.** `ordertag` has a modest length limit **[VOLATILE — verify]**, so the tag is a compact encoding (e.g. Crockford base32 of a ULID suffix) rather than a raw UUID. Every order write path must be unique-indexed on the idempotency key so that even a buggy caller cannot double-insert. Resolution of `UNKNOWN` orders is blocking: the session will not resume trading an instrument with an unresolved order on it.

**Cost.** None.

### Decision 8 — Limit orders only; no market orders anywhere in the codebase

**Decision.** The `OrderType` domain enum contains `LIMIT` and `STOPLOSS_LIMIT` only. `MARKET` and `IOC` are absent from the type system. Urgency is expressed as a **marketable limit** — LTP ± a configured buffer, clamped to tick size and circuit limits.

**Reason.** The NSE framework prohibits market and IOC orders for algo trading (§1.1). Making them unrepresentable in the domain model is stronger than validating against them: a strategy author cannot express a forbidden order even by accident, and there is no runtime path that can emit one.

**Alternatives considered.** Supporting market orders with a runtime guard (rejected: a guard can be bypassed, misconfigured, or disabled; the type system cannot). Supporting them for paper trading only (rejected: paper and live must run identical code paths, or paper trading stops being evidence about live behaviour).

**Operational implications.** Every strategy must reason about non-fills. The execution engine needs a repricing policy — a limit order unfilled after N seconds is cancelled and replaced at a fresh marketable price, bounded by a maximum chase distance and a maximum number of reprices, both risk-limited. This is genuinely more complex than market orders and must be tested explicitly.

**Cost.** None financially; some slippage relative to hypothetical market orders, which the backtest cost model must reflect honestly.

### Decision 9 — Terraform for infrastructure as code

**Decision.** Terraform, with remote state in S3 using native S3 state locking (Terraform ≥1.10 — no DynamoDB lock table needed).

**Reason.** The estate is EC2, EIP, VPC, IAM, SSM parameters, S3, CloudWatch alarms, and SNS — classic Terraform territory with mature providers. CDK and SAM are optimised for serverless stacks that we deliberately do not have. Terraform's `prevent_destroy` lifecycle rule is important here specifically because of the Elastic IP: the once-weekly IP change limit makes accidental EIP destruction a multi-day trading outage.

**Alternatives considered.** AWS CDK (rejected: its advantage is composing serverless constructs in a general-purpose language; with zero Lambda that advantage disappears, and it adds a Node toolchain to a Python project). SAM (rejected: purpose-built for serverless; we have none). Raw CloudFormation (rejected: verbose, weaker module ecosystem). Console clicking (rejected: unreproducible, and rebuilding from an AMI must be a documented, tested path).

**Operational implications.** Terraform state lives in S3 and must itself be backed up. Native S3 locking saves the DynamoDB table.

**Cost.** ~$0.10/mo for state storage.

### Decision 10 — SSM Parameter Store over Secrets Manager

**Decision.** Secrets in SSM Parameter Store as `SecureString` with the default KMS key, read at startup via the EC2 instance role. No secrets in Git, environment files committed to the repo, or AMIs.

**Reason.** Parameter Store `SecureString` is free at Standard tier; Secrets Manager is $0.40 per secret per month. We need roughly five secrets (API key, client code, PIN, TOTP seed, Mongo URI) — $2/mo, about 10% of the entire budget, for automatic rotation we cannot use anyway (Angel One offers no rotation API, and the TOTP seed is fixed at enrolment).

**Alternatives considered.** Secrets Manager (rejected: pays for rotation that does not exist for this provider). `.env` on the instance (rejected: unencrypted at rest, survives snapshots, no audit trail). Baking into the AMI (rejected: unrotatable, leaks to anyone with image access).

**Operational implications.** No automatic rotation — a documented manual rotation runbook is required. Parameter reads are logged via CloudTrail, giving an audit trail.

**Cost.** $0/mo versus ~$2/mo.

### Decision 12 — Next.js dashboard as primary interface, via a command bus the worker owns

**Decision.** A **Next.js dashboard is the primary control surface**. It talks to a FastAPI control service on the same box. That service **never calls the broker** — for any state-changing action it writes a document to a `commands` collection, which the worker consumes, validates through the **same risk engine as any strategy signal**, and executes. Reads go straight to MongoDB through a read-only user. The CLI survives as an emergency and CI path only.

**Reason.** The brief's §41 principle is `Dashboard → directly → Angel One` must never happen, and every live order must pass through risk and execution. A dashboard that calls a broker API directly would have its own auth handling, its own error handling, its own rate-limit accounting and its own bugs — a second, less-tested path to real money. Routing control through a command bus means there is exactly **one** process that holds broker credentials (Decision 3), **one** rate limiter, **one** idempotency mechanism, and **one** audit trail. A "square off everything" click and a strategy's exit signal traverse identical code.

It also makes control actions durable and auditable for free: a command is a persisted document with a requester, a timestamp, a status and a result, so "who flattened my book at 14:32" is answerable.

**Alternatives considered.** Dashboard calling a synchronous control endpoint on the worker (rejected: couples the UI's availability to the worker's event loop, and a blocking HTTP handler inside the trading process risks stalling the market-data loop — the command bus decouples them with no added infrastructure since MongoDB is already there). Next.js API routes talking to Mongo directly for writes (rejected: puts business validation in the UI tier, where it will drift from the worker's rules). Dashboard holding broker credentials for "read-only" account queries (rejected: a second session conflicts with Angel One's per-client-code session limit, and credentials in a web tier is precisely the exposure we are avoiding). SQS for the command bus (rejected: MongoDB gives us ordering, durability, status tracking and query in one place we already pay for).

**Operational implications.** Commands are **not instantaneous** — worst case is one poll interval (1s target, via change streams where available). The UI must show command state (`pending → accepted → executing → done/failed/rejected`) rather than pretending a click succeeded. Every command carries an idempotency key, so a double-click or a retry cannot execute twice.

**Safety-critical caveat:** the **kill switch must not depend on the dashboard**. It remains reachable via CLI over SSM Session Manager, and ultimately via stopping the instance. A control surface that can fail is not an acceptable sole path to "stop trading".

**Cost.** ~$0 (static export adds no meaningful RAM; Cloudflare Tunnel and Access are free; a domain is ~$10/yr).

### Decision 13 — Next.js on Vercel; API published at `empapi.journeymen.in` behind Caddy

**Decision.** The Next.js dashboard is hosted on **Vercel** (deployed by the operator, not by our CI). The control API is published at **`empapi.journeymen.in`** via an A record pointing at the Elastic IP, with **Caddy** terminating TLS using auto-renewed Let's Encrypt certificates. Inbound **443 only** is opened on the security group. **All dashboard data flows through the API** — the dashboard never connects to MongoDB Atlas directly.

**Reason.** Hosting the UI on Vercel removes it from the 2 GB box entirely, which frees the RAM budget and lifts the constraint that forced a static export — SSR, server components and ISR are all available now. Publishing the API under the already-owned `journeymen.in` avoids a DNS migration, and Caddy's TLS automation makes certificate management a non-issue.

Keeping every read on the API path is the more consequential half. Vercel's egress IPs are dynamic, so a dashboard talking to Atlas directly would require allowlisting `0.0.0.0/0` on the database holding the entire order history. Routing through the API keeps **Atlas locked to the single Elastic IP**, and keeps authorization and query logic in one place rather than drifting between the worker and the UI.

**Alternatives considered.** Cloudflare Tunnel (genuinely better on security — it needs no inbound port at all — but requires moving `journeymen.in` to Cloudflare nameservers, which the operator declined; documented as the upgrade path if the open listener ever proves troublesome). Cloudflare proxied A record (needs CF DNS anyway, so the tunnel dominates it). Public ALB + ACM (~$16/mo, over half the infrastructure budget, for TLS that Caddy does free). Static export served from the box (superseded — the UI is no longer on the box). Direct Atlas access from Vercel server components (rejected above).

**Operational implications — this is the significant trade.** The trading box now has a **public listener**, which is a real change from the previous zero-inbound posture. Three compensating controls are therefore mandatory, not optional:

1. **App-layer auth is the only perimeter.** There is no network-level filter possible, because Vercel's egress is dynamic. See §16.
2. **Resource isolation.** Caddy and the API run as separate containers with explicit CPU and memory limits, and the worker gets reserved CPU shares. A flood against the public endpoint must not be able to starve the trading loop — availability of the API is expendable, availability of the worker is not.
3. **Rate limiting and abuse alarms** at Caddy and in the API, with CloudWatch alarms on 4xx/5xx spikes and repeated auth failures.

Caddy uses the **TLS-ALPN-01** challenge so only port 443 needs to be open — port 80 stays closed. CORS is restricted to the single production dashboard origin; Vercel preview deployments get random URLs and are **not** allowed against the production API.

**Cost.** Vercel Hobby: free. Domain: already owned. Caddy: free. Net **$0**.

### Decision 11 — Modular monolith, in-process synchronous event bus

**Decision.** One deployable process, strict module boundaries, a typed in-process event bus with synchronous handlers. Material events are additionally appended to a `system_events` collection with a correlation ID.

**Reason.** The brief explicitly warns against over-engineering, and the workload is one user, one account, a few hundred instruments. Ordering matters intensely in trading (a fill must be applied before the next signal is evaluated), and synchronous in-process dispatch gives total ordering for free. SQS gives at-least-once delivery with no ordering (or FIFO with throughput limits and added cost), forcing us to build deduplication and sequencing we would otherwise get by construction.

**Alternatives considered.** SQS/EventBridge between modules (rejected: adds network hops, eventual consistency, and dedup burden to a single-process workload; the persistence layer already provides durability). Kafka (rejected: the brief forbids it and it is absurd at this scale). In-process `asyncio` queues between modules (partially adopted — the feed reader hands off to the processing loop via a bounded queue so a slow strategy cannot stall the socket reader, but module-to-module logic stays synchronous and ordered).

**Operational implications.** A slow or crashing strategy handler can stall the pipeline, so handlers run under a timeout and an exception in one strategy must not kill the session — it halts that strategy and alerts.

**Cost.** $0.

---

## 4. Repository structure

```
emporos/
├── Pipfile                      # source of truth for dependencies
├── Pipfile.lock                 # committed, deterministic
├── pyproject.toml               # ruff / mypy / pytest config only (no deps)
├── .env.example
├── README.md
├── plan.md                      # this document
│
├── config/
│   ├── settings.base.yaml
│   ├── settings.local.yaml
│   ├── settings.staging.yaml
│   ├── settings.production.yaml
│   ├── fees/angelone-equity-2026.yaml    # dated fee schedules
│   ├── calendars/nse-2026.yaml           # trading holidays
│   └── strategies/*.yaml                 # strategy params, no secrets
│
├── src/emporos/
│   ├── core/            # config loader, clock, ULID/ids, errors, event bus, enums
│   ├── domain/          # PURE: Instrument, Tick, Candle, Signal, Order, Fill,
│   │                    #       Position, Money, Quantity. No I/O, no imports from
│   │                    #       any other emporos package. Fully unit-testable.
│   ├── broker/
│   │   ├── base.py           # Broker ABC — the only interface strategies may see
│   │   ├── models.py         # broker-neutral request/response DTOs
│   │   ├── errors.py         # typed, classified broker errors
│   │   ├── ratelimit.py      # token-bucket per endpoint group
│   │   ├── angelone/
│   │   │   ├── auth.py       # TOTP, login, session, daily re-auth
│   │   │   ├── rest.py       # httpx transport, retry/backoff
│   │   │   ├── ws_market.py  # SmartWebSocketV2 client + binary parser
│   │   │   ├── ws_orders.py  # smart-order-update client
│   │   │   ├── mapping.py    # domain ⇄ SmartAPI field translation
│   │   │   └── adapter.py    # AngelOneBroker(Broker)
│   │   └── paper/
│   │       └── adapter.py    # PaperBroker(Broker), same interface
│   │
│   ├── instruments/     # master sync, versioning, token↔symbol resolution
│   ├── marketdata/      # feed manager, normalizer, aggregator, staleness watchdog
│   ├── history/         # backfill orchestrator, gap detection, S3 archive
│   ├── strategies/      # Strategy ABC, registry, indicators, builtin/
│   ├── signals/         # signal model + persistence
│   ├── risk/            # engine + individual rule classes + kill switch
│   ├── execution/       # engine, state machine, idempotency, repricing
│   ├── portfolio/       # positions, P&L, snapshots, reconciler
│   ├── backtest/        # engine, SimulatedBroker, cost model, metrics, walkforward
│   ├── persistence/     # Mongo client, repositories, migrations, S3 store
│   ├── session/         # lifecycle state machine, daily orchestrator, scheduler
│   ├── observability/   # structured logging, CloudWatch metrics, alerts, health
│   ├── control/         # command models, validation, worker-side CommandProcessor
│   ├── api/             # FastAPI control service (reads RO Mongo, writes commands)
│   └── cli/             # typer entrypoints — emergency + CI path, NOT primary UI
│
├── dashboard/           # Next.js App Router, TypeScript — deployed to Vercel
│   ├── app/             # routes: overview, positions, orders, strategies,
│   │                    #         backtests, risk, market, system
│   ├── components/      # shadcn/ui + charts
│   ├── lib/api/         # typed client generated from the FastAPI OpenAPI schema
│   └── lib/auth.ts      # passcode login → JWT, token storage/refresh
│
├── tests/
│   ├── unit/            # pure, fast, no I/O
│   ├── integration/     # real Atlas `emporos_dev` via MONGO_URL (§6.0)
│   ├── contract/        # adapter conforms to Broker ABC; fixtures vs SDK oracle
│   ├── regression/      # golden-file backtest outputs
│   └── failure/         # chaos: disconnects, dupes, stale data, restarts
│
├── scripts/             # one-off ops scripts (seed calendar, rotate secrets…)
├── infra/terraform/     # vpc, ec2, eip, iam, ssm, s3, cloudwatch, sns
├── docker/              # Dockerfile (arm64), compose.yml for local dev
├── docs/                # the 17 deliverable documents
└── .github/workflows/   # ci.yml, deploy.yml
```

**Enforced dependency rule:** `domain` imports nothing from `emporos`. `strategies` may import `domain` and `broker.base` but **never** `broker.angelone`. A CI lint check (import-linter) enforces this, so the "strategy never knows Angel One exists" principle is mechanically guaranteed rather than merely documented.

---

## 5. Broker abstraction

```python
# src/emporos/broker/base.py
class Broker(ABC):
    # --- session ---
    @abstractmethod async def authenticate(self) -> Session: ...
    @abstractmethod async def ensure_session(self) -> Session: ...   # idempotent; re-login if expired
    @abstractmethod async def logout(self) -> None: ...
    @abstractmethod async def get_profile(self) -> Profile: ...

    # --- reference data ---
    @abstractmethod async def get_instruments(self) -> Iterable[InstrumentRecord]: ...

    # --- market data ---
    @abstractmethod async def get_quote(self, instruments: Sequence[InstrumentId]) -> list[Quote]: ...
    @abstractmethod async def get_historical_candles(self, req: CandleRequest) -> list[Candle]: ...
    @abstractmethod async def subscribe_market_data(self, instruments, mode) -> None: ...
    @abstractmethod async def unsubscribe_market_data(self, instruments) -> None: ...
    @abstractmethod def on_tick(self, handler: Callable[[Tick], None]) -> None: ...

    # --- orders ---
    @abstractmethod async def place_order(self, req: PlaceOrderRequest) -> BrokerOrderAck: ...
    @abstractmethod async def modify_order(self, req: ModifyOrderRequest) -> BrokerOrderAck: ...
    @abstractmethod async def cancel_order(self, req: CancelOrderRequest) -> BrokerOrderAck: ...
    @abstractmethod async def get_order_book(self) -> list[BrokerOrder]: ...
    @abstractmethod async def get_trade_book(self) -> list[BrokerTrade]: ...
    @abstractmethod async def find_orders_by_tag(self, tag: str) -> list[BrokerOrder]: ...   # idempotency
    @abstractmethod def on_order_update(self, handler: Callable[[BrokerOrderUpdate], None]) -> None: ...

    # --- account ---
    @abstractmethod async def get_positions(self) -> list[BrokerPosition]: ...
    @abstractmethod async def get_holdings(self) -> list[BrokerHolding]: ...
    @abstractmethod async def get_funds(self) -> Funds: ...
```

Implementations: `AngelOneBroker`, `PaperBroker`, `SimulatedBroker` (backtest). All three satisfy the same contract test suite (`tests/contract/`), which is the mechanism guaranteeing that a strategy behaves identically across backtest, paper, and live.

`find_orders_by_tag` exists solely to serve Decision 7 and is the reason idempotency works.

---

## 6. MongoDB data model

### 6.0 Environment and database selection

**One connection string, environment-selected database — no local Mongo container, no separate dev setup.**

A single `MONGO_URL` (operator-provided, an Atlas connection string) is used everywhere. The database name is chosen at startup purely by the `ENV` environment variable:

```python
DB_NAME = "emporos" if os.environ.get("ENV") == "main" else "emporos_dev"
```

| `ENV` value | Resolved environment | Database |
|---|---|---|
| `main` | production | `emporos` |
| anything else (unset, `local`, `dev`, `staging`, CI) | non-production | `emporos_dev` |

This replaces the earlier plan to run a local Dockerized MongoDB for development and integration tests (Decision 5's self-hosted-vs-Atlas question is a **separate, production-only** decision at Phase 19 and is unaffected). Local development, CI, and integration tests all run against the real Atlas `emporos_dev` database through the same client code path production uses against `emporos` — the only variable is which name gets selected. This is a strictly stronger test than a local container: it exercises the actual Atlas topology (replica set, network latency, auth) that production will see, with zero local setup.

**The safety implication is load-bearing, not incidental:** because `ENV=main` is the *only* condition that resolves to the production database, an operator who forgets to set `ENV` on a laptop, in a CI runner, or in a misconfigured deployment fails safe into `emporos_dev` — never the reverse. Production access requires an explicit, deliberate `ENV=main`. This is the same fail-safe shape as `LIVE_TRADING_ENABLED` defaulting to `false` (§20/§38), applied to data access: the dangerous state must be opted into, never fallen into.

**Accepted trade-off — shared dev database.** `emporos_dev` is one database shared by every local developer, every CI run, and every integration test — there is no per-run or per-branch isolation. At single-operator scale this is a simplification worth making, but it means concurrent CI runs and local test sessions can, in principle, collide on the same documents. Mitigation is cheap and deferred rather than built now: test fixtures should use randomly-suffixed IDs (already true anywhere ULIDs are used) and clean up after themselves; if CI ever runs jobs in parallel, revisit with a per-run collection prefix or a scratch database rather than reintroducing local containers.

`docker/compose.yml` (§4) accordingly carries no MongoDB service — only anything else local dev genuinely needs (e.g. a MinIO container for S3-abstraction tests, kept independent of this decision).

Database `emporos` (or `emporos_dev`, per the selection above). All timestamps stored UTC; all market-facing logic converts to `Asia/Kolkata` at the boundary. Money as `Decimal128`, never float.

| Collection | Purpose | Key indexes | TTL / retention | Write freq | Read pattern |
|---|---|---|---|---|---|
| `users` | Degenerate single user | `_id` | none | ~never | startup |
| `accounts` | Broker account, limits, mode | unique `client_code` | none | rare | startup |
| `instruments` | Current instrument master | unique `{exchange, token}`; `{tradingsymbol, exchange}`; `{name}` | replaced daily | ~4k/day | lookup by token (hot, cached in memory) |
| `instrument_versions` | Append-only history of instrument metadata changes | `{token, exchange, valid_from}`; `{valid_to}` | keep forever (small) | on change only | historical trade interpretation |
| `candles` | OHLCV bars, all timeframes | **unique** `{instrument_id, tf, ts}`; `{tf, ts}` | 1m: 90d rolling → S3; 5m/15m/1h: 1y; 1d: forever | ~75k/day | range scans by instrument+tf |
| `ticks` (optional) | Raw tick archive | time-series, meta `instrument_id`, time `ts` | `expireAfterSeconds` 7d | high | debugging only |
| `strategies` | Strategy definitions + current config | unique `name` | none | rare | startup |
| `strategy_runs` | One doc per strategy per session; **config snapshot** | `{strategy_id, session_date}` | 2y | ~5/day | reporting, reproducibility |
| `signals` | Every signal, incl. rejected | `{strategy_run_id, ts}`; `{instrument_id, ts}` | 1y | ~100/day | audit, analytics |
| `orders` | Order lifecycle, current state | **unique** `idempotency_key`; **unique** `ordertag`; `{broker_order_id}`; `{state, session_date}` | forever | ~50/day | reconciliation, audit |
| `order_events` | Append-only state transitions | `{order_id, seq}` | forever | ~300/day | audit trail, debugging |
| `executions` | Fills | **unique** `{broker_trade_id}`; `{order_id}` | forever | ~80/day | P&L, reconciliation |
| `positions` | Current open positions | unique `{account_id, instrument_id}` | none | on fill | hot read |
| `portfolio_snapshots` | EOD and intraday snapshots | `{account_id, ts}` | 5y | ~10/day | dashboard, analytics |
| `risk_events` | Every rejection and breach | `{ts}`; `{rule, ts}` | 2y | low | alerting, audit |
| `reconciliation_runs` | Result of each reconcile pass | `{ts}`; `{status}` | 1y | ~20/day | ops |
| `backtest_runs` | Params, config hash, metrics | `{strategy_id, created_at}` | forever | ad hoc | research |
| `backtest_trades` | Per-trade detail | `{backtest_run_id}` | tied to run | ad hoc | research |
| `system_events` | Structured event log | `{ts}`; `{correlation_id}`; `{type, ts}` | TTL 30d | high | debugging |
| `market_calendar` | Trading days, holidays, session times | unique `{date}` | forever | annual | every session start |
| `kill_switch` | Single doc; halt flags | `_id` | none | rare | polled every 5s |
| `commands` | Dashboard → worker control bus | **unique** `idempotency_key`; `{status, created_at}`; `{type, created_at}` | 1y | low | change stream / 1s poll |
| `command_results` | Outcome + error detail per command | `{command_id}` | 1y | low | dashboard status display |

**Critical index rationale.**

- `orders.idempotency_key` unique — the database-level guarantee that a duplicate order cannot be created even by a buggy code path. This is the last line of defence behind Decision 7.
- `orders.ordertag` unique — the correlation handle must be unambiguous when resolving `UNKNOWN` orders against the broker's order book.
- `executions.broker_trade_id` unique — order-update messages can be redelivered on reconnect; this makes fill processing idempotent by construction.
- `candles` unique compound — idempotent upsert of bars (see Decision 6).

**Connection settings.** Single `AsyncMongoClient` for the process lifetime, `maxPoolSize=20`, `retryWrites=true`, `w="majority"`, `readConcern="majority"` for reconciliation reads, `serverSelectionTimeoutMS=5000`. Multi-document transactions are used in exactly one place — applying a fill (update position + insert execution + update order) — and are otherwise avoided.

> **Retention is hosting-dependent (Decision 5).** The `candles` retention figures above assume Atlas M0/Flex, where storage is capped and cold data must tier to S3. If Phase 19 selects self-hosted MongoDB on EBS, the 1m window widens from 90 days to "keep everything" and the S3 tier becomes an archive rather than a dependency. **All candle reads and writes must therefore go through `CandleRepository`**, which owns the hot/cold union — no caller may query the `candles` collection directly. This is what makes the Phase 17 decision a config change. A single-AZ self-hosted deployment also means `w="majority"` degrades to a single-node acknowledgement; the write concern is configuration, not a hardcoded literal.

---

## 7. Market data pipeline

```
SmartWebSocketV2 (1 connection, ≤200 tokens, LTP or QUOTE mode)
        │  binary frames, little-endian
        ▼
   Parser  ──────▶ malformed frame → count, log, drop (never crash the reader)
        │
        ▼
  Bounded asyncio.Queue (maxsize ~10k; overflow = drop-oldest + alarm)
        │
        ▼
  Normalizer ──▶ Tick{instrument_id, exch_ts, recv_ts, ltp, volume, seq}
        │        · token → instrument_id via in-memory map
        │        · exchange epoch (ms, IST) → UTC datetime
        │        · drop ticks outside session window
        │        · drop duplicate (token, exch_ts, ltp) within 1s
        │        · out-of-order (exch_ts < last seen): count, use for trade
        │          record but never rewrite a closed candle
        ▼
   ┌────┴─────────────────┬──────────────────┬─────────────────┐
   ▼                      ▼                  ▼                 ▼
Strategy Engine    Candle Aggregator   Staleness Watchdog   Tick Archive
(on_market_data)   (1m → 5m/15m/1h)    (per-instrument)     (optional, jsonl.gz
                          │                   │               → S3 nightly)
                          ▼                   ▼
                     candles (Mongo)     STALE → halt entries
```

**Aggregation.** Bars are built in memory and closed on a wall-clock boundary, not on the arrival of the next tick — otherwise an illiquid instrument's bar never closes. A closed bar is written immediately via idempotent upsert. Higher timeframes are derived from closed 1m bars, never from ticks directly, so they are consistent by construction.

**Reconnect recovery.** On reconnect: re-authenticate if the feed token expired, resubscribe the full token set, then **backfill the gap from `getCandleData`** for the disconnected window and upsert (safe by Decision 6). Any bar covering a gap is flagged `partial: true`, and strategies may opt to ignore partial bars.

**Staleness.** Per instrument, `now - last_tick_ts`. Threshold is configurable and liquidity-aware (a thin stock legitimately goes quiet). Breach sets `STALE`, which blocks new entries but permits exits — being unable to exit because data went quiet is more dangerous than the stale data itself.

**Session handling.** 09:15–15:30 IST regular session; pre-open 09:00–09:15 handled by ignoring ticks before 09:15 unless explicitly enabled. Holidays from `market_calendar`. A sanity alarm fires if no ticks have arrived by 09:16 on an expected trading day — this catches both a broken feed and a stale calendar.

**Retention tiers** (Atlas M0/Flex baseline — widens if Phase 19 selects self-hosting, per Decision 5):

```
ticks      → memory + optional 7d TTL / S3 archive   (debugging only)
1m candles → Mongo 90d rolling → S3 Parquet forever   (strategy + backtest)
5m/15m/1h  → Mongo 1y → S3                            (derived, cheap to rebuild)
1d candles → Mongo forever (small)                    (screening, long backtests)
```

The 90-day window is sized so a 200-instrument watchlist (~0.8 GB) fits Flex comfortably; under M0's 512 MB it tightens to ~30 days over ~50 instruments during the build, which is ample for paper trading. Backtests read through `CandleRepository`, which unions hot Mongo with cold S3 behind a local disk cache, so strategy and backtest code never knows which tier served a bar.

---

## 8. Instrument master

Daily sync from `OpenAPIScripMaster.json`, filtered to `exch_seg ∈ {NSE, BSE}` and cash-segment instrument types.

Pipeline: download → validate (row count within ±20% of yesterday, required fields present, tick size and lot size sane) → diff against current → write changed rows to `instrument_versions` with `valid_from`/`valid_to` → atomically replace `instruments`.

**The validation gate matters.** A truncated or malformed upstream file that silently wipes the instrument master would take down trading. If validation fails, we keep yesterday's master, alert, and continue — stale instruments are far safer than none.

**Versioning.** Tokens are reused by exchanges over time, and tradingsymbols change on corporate actions. `instrument_versions` lets a trade from six months ago still resolve to the instrument as it was defined then. Orders store `instrument_id` plus the `instrument_version_id` in force at the time.

**No hardcoded tokens anywhere.** Strategies reference instruments by `(exchange, tradingsymbol)` and resolve through the registry. A CI grep check fails the build on numeric literals that look like tokens in strategy code.

---

## 9. Strategy framework

```python
class Strategy(ABC):
    def initialize(self, ctx: StrategyContext) -> None: ...
    def on_market_data(self, bar: Candle | Tick) -> None: ...
    def generate_signal(self) -> Signal | None: ...
    def on_order_update(self, update: OrderUpdate) -> None: ...
    def on_session_end(self) -> None: ...
    def on_shutdown(self) -> None: ...
```

`StrategyContext` exposes only: indicator/history access, current positions **for that strategy**, a clock, a logger, and config. It exposes **no broker handle** — a strategy physically cannot place an order. It returns signals; risk and execution decide what happens.

**Determinism.** Strategies may not call `datetime.now()` (use `ctx.clock`), may not perform I/O, and may not use unseeded randomness. A CI lint rule enforces the first two. This is what makes backtest results reproducible and makes "same code in backtest, paper, and live" a real guarantee rather than an aspiration.

**Configuration.** YAML in `config/strategies/`, versioned in Git so changes are reviewable and diffable. At `strategy_run` start the fully-resolved config is **snapshotted into MongoDB** with a hash, so a run can always be reproduced even after the YAML changes. Secrets never appear in strategy config.

```yaml
name: momentum_v1
enabled: true
timeframe: 5m
universe: { type: static, instruments: ["NSE:RELIANCE-EQ", "NSE:TCS-EQ"] }
parameters: { fast_ema: 20, slow_ema: 50, rsi_period: 14 }
risk:
  max_position_value: 50000
  max_open_positions: 3
  stop_loss_pct: 1.0
  target_pct: 2.0
execution:
  limit_buffer_bps: 5        # marketable-limit offset (Decision 8)
  reprice_after_seconds: 30
  max_reprices: 3
session:
  no_new_entries_after: "15:00"
  square_off_at: "15:15"
```

---

## 10. Backtesting

```
S3/Mongo candles → DataFeed → Strategy → Signal → Risk → SimulatedBroker
                                                              │
                                                    Fills + costs
                                                              ▼
                                                     Portfolio → Metrics
```

**Bias prevention, enforced structurally:**

- **Look-ahead:** the feed yields bar *t* to the strategy only after bar *t* has closed; orders generated on bar *t* can fill no earlier than bar *t+1*. The feed physically cannot hand out future bars — it is an iterator over closed bars, not a random-access frame.
- **Data leakage:** indicator warm-up consumes a prefix that produces no signals.
- **Survivorship:** the universe is resolved from `instrument_versions` as of the simulated date, so delisted names are present in historical runs.

**Cost model** — pluggable, driven by a dated YAML fee schedule (`config/fees/`), never hardcoded, because these rates change:

brokerage (Angel One slab), STT (intraday sell vs delivery both sides), exchange transaction charges, SEBI turnover fee, stamp duty (buy side), GST 18% on (brokerage + transaction + SEBI), DP charges on delivery sells. **[VOLATILE — verify every rate against Angel One's current tariff before trusting a backtest.]**

**Fill model.** Limit orders fill only if the bar's price range actually traded through the limit; configurable partial fills; configurable slippage; rejected orders simulated at a configurable rate. Conservative defaults — an optimistic fill model is the most common way backtests lie.

**Metrics.** CAGR, total return, max drawdown + drawdown periods, Sharpe, Sortino, Calmar, win rate, profit factor, expectancy, turnover, trade count, average trade, exposure, consecutive wins/losses, monthly return table.

**Walk-forward.** Rolling train/validate/test windows with a strict temporal boundary; parameter selection sees only the training window; the test window is scored once. An anchored and a rolling mode. Leakage detection: a run whose test period overlaps its training period fails loudly rather than reporting a flattering number.

---

## 11. Risk engine

Every signal passes through the risk engine immediately before execution. The engine is a pure, ordered list of rules over an immutable snapshot of account state — making it exhaustively unit-testable, which matters because this is the subsystem that prevents catastrophe.

| Rule | Blocks |
|---|---|
| `TradingModeGuard` | live orders when `LIVE_TRADING_ENABLED != true` |
| `KillSwitchGuard` | everything when the kill switch is set |
| `MarketSessionGuard` | orders outside the session window / on holidays |
| `StaleDataGuard` | entries when the instrument's feed is stale (exits allowed) |
| `BrokerHealthGuard` | orders when the broker session or order feed is unhealthy |
| `ReconciliationGuard` | orders while reconciliation is pending or has failed |
| `DuplicateOrderGuard` | a second live order on the same instrument+side within a window |
| `MaxDailyLossGuard` | everything once realised+unrealised daily loss exceeds the cap |
| `MaxStrategyLossGuard` | that strategy once its loss cap is hit |
| `MaxPositionValueGuard` | orders exceeding per-instrument exposure |
| `MaxOpenPositionsGuard` | new entries beyond the position count cap |
| `MaxCapitalDeployedGuard` | orders exceeding total deployed capital |
| `MaxOrderQuantityGuard` | fat-finger quantity |
| `PriceSanityGuard` | limit price outside circuit limits or >N% from LTP |
| `AbnormalSpreadGuard` | entries when the bid/ask spread is abnormal |
| `OrderRateGuard` | orders exceeding our self-imposed OPS budget |

Rejections are **always persisted** to `risk_events` with the full evaluation context — a silently dropped signal is unauditable, and post-mortems depend on knowing why a trade did not happen.

**Kill switch.** A MongoDB flag polled every 5 seconds plus a local file sentinel. Setting it halts new orders immediately and optionally squares off. Settable from the CLI, from the dashboard later, and — as a hard fallback if the process is wedged — by stopping the instance via SSM. Required by the NSE framework (§1.1).

---

## 12. Execution engine and order state machine

```
                    CREATED
                       │ risk evaluation
            ┌──────────┴──────────┐
     RISK_REJECTED           RISK_APPROVED
        (terminal)                │ write intent to Mongo FIRST
                                  ▼
                            PENDING_NEW ──────────┐ ambiguous response
                                  │               ▼
                    broker ack    │            UNKNOWN
                                  │               │ resolve via find_orders_by_tag
                                  ▼               │
                                 OPEN ◀───────────┘
                                  │
          ┌───────────┬───────────┼────────────┬─────────────┐
          ▼           ▼           ▼            ▼             ▼
  PARTIALLY_FILLED  FILLED   PENDING_CANCEL PENDING_MODIFY REJECTED
          │         (term.)       │            │           (term.)
          └────▶ FILLED       CANCELLED      OPEN
                              (terminal)
```

**Every transition is appended to `order_events` with a monotonic sequence number.** The order document holds current state; the event log holds how it got there. Reconstructing state from events is possible, which makes debugging a mis-executed trade tractable.

**Placement protocol (the duplicate-prevention core):**

1. Generate `idempotency_key` (ULID) and `ordertag` (compact encoding of it).
2. **Insert `orders` doc in `PENDING_NEW`** — the unique index on `idempotency_key` makes a double-insert impossible.
3. Acquire an OPS token from the rate limiter.
4. Call `place_order` with the tag.
5. Outcomes:
   - **Success** → record `broker_order_id`, transition to `OPEN`.
   - **Definitive rejection** (validation error, insufficient margin) → `REJECTED`.
   - **Ambiguous** (timeout, connection reset, 5xx, unparseable) → transition to `UNKNOWN`. **Never retry the placement.** Schedule resolution: call `find_orders_by_tag(tag)`; if an order exists, adopt it and go `OPEN`; if not, after a bounded confirmation window with repeated checks, go `REJECTED` and allow the strategy to re-signal.
6. While any order on an instrument is `UNKNOWN`, that instrument is frozen for new orders.

**Repricing** (necessary because market orders are banned, Decision 8): an unfilled limit after `reprice_after_seconds` is cancelled and replaced at a fresh marketable price, bounded by `max_reprices` and a maximum chase distance. Each reprice is a new order with a new idempotency key, linked via `parent_order_id`. Cancel-then-replace, never modify-in-place, because a modify racing a fill is ambiguous in a way cancel-then-confirm is not.

**Fill processing** is idempotent via the unique index on `executions.broker_trade_id`, so redelivered order-update messages after a reconnect are harmless.

---

## 13. Reconciliation, session lifecycle, and recovery

**Reconciliation.** The broker is the source of truth. Runs at startup (blocking — no trading until clean), every 5 minutes during the session, and at EOD.

Compares internal `orders`/`positions`/`executions` against `get_order_book`, `get_trade_book`, `get_positions`. Detects: orders we have that the broker does not (and vice versa — including **manual orders placed from the Angel One app**, which the postback does not cover per §1.6), quantity and price mismatches, missing fills, orphaned positions. Discrepancies are written to `reconciliation_runs`, alerted, and — depending on severity — halt trading. Auto-heal is limited to adopting broker state for fills and positions; anything structurally ambiguous halts for human decision, because guessing about money is worse than stopping.

**Session lifecycle.**

```
STARTING → AUTHENTICATING → RECOVERING → CONNECTING → READY
              → TRADING ⇄ HALTED → SQUARING_OFF → RECONCILING
              → REPORTING → SHUTTING_DOWN        (any state → FAILED)
```

- **Pre-open (~08:45):** load config, authenticate (fresh TOTP login — mandatory, sessions die at midnight), sync instrument master, **RECOVERING**: load unterminated orders from Mongo and resolve every `UNKNOWN` against the broker, reconcile positions, validate risk limits, warm up indicators from history, connect both WebSockets, verify tick freshness. Only then `READY`.
- **Session (09:15–15:30):** stream → aggregate → strategies → signals → risk → execute → process updates → persist → 5-minute reconcile → heartbeat metrics.
- **Pre-close:** stop new entries at the configured time, square off intraday positions at `square_off_at`, confirm flat.
- **Post-close:** final reconcile, P&L, snapshot, metrics, rollup candles to S3, EOD report by email, archive logs.

**Recovery matrix.**

| Failure | Response |
|---|---|
| Process crash | systemd restarts; enters `RECOVERING`; resolves `UNKNOWN` orders before any trading |
| EC2 reboot / stop | Instance auto-starts container; same recovery path; alarm on heartbeat gap |
| Market-data WS drop | Exponential backoff reconnect, resubscribe, backfill gap via `getCandleData`, mark bars partial |
| Order-update WS drop | Reconnect; immediately full-reconcile against order book (updates missed while down) |
| Auth expiry / midnight logout | Detect 401 class, re-login with fresh TOTP, refresh feed token, resubscribe |
| MongoDB outage | Buffer writes to local disk queue, halt **new** orders (cannot guarantee idempotency without the unique index), continue managing existing positions, drain on recovery |
| SmartAPI outage | Halt; alert; retain positions; do not square off blindly into an outage |
| Stale market data | Block entries, allow exits, alert |
| Duplicate order update | Idempotent by unique `broker_trade_id` |
| Rate-limit false positive | Exponential backoff with jitter; do not treat as a real limit (§1.4) |

**Restart safety is the invariant:** because intent is written before the broker call and every ambiguous outcome resolves by querying the broker rather than retrying, no restart path can place a duplicate order.

---

## 14. Observability

**Logging.** Structured JSON to stdout (Docker → CloudWatch Logs agent). Every record carries `correlation_id`, and where applicable `session_id`, `strategy_id`, `signal_id`, `order_id`, `instrument_id`. A signal is traceable end-to-end through risk, execution, fill, and P&L by one correlation ID. Log retention 30 days (cost control).

**Metrics** (CloudWatch custom, kept to a deliberately small set — $0.30/metric/month):

`WorkerHeartbeat`, `TicksPerMinute`, `MaxDataStalenessSeconds`, `OpenOrders`, `UnknownOrders`, `DailyPnL`, `RiskRejections`, `ReconciliationMismatches`.

**Alarms → SNS → email.**

| Alarm | Trigger |
|---|---|
| Worker stopped (dead-man's switch) | `WorkerHeartbeat` missing 3 periods during market hours |
| Market data stale | `MaxDataStalenessSeconds` > threshold |
| No data at open | `TicksPerMinute` = 0 at 09:16 on a trading day |
| Unknown orders | `UnknownOrders` > 0 for 2 periods |
| Daily loss breach | `DailyPnL` < limit |
| Reconciliation mismatch | `ReconciliationMismatches` > 0 |
| Auth failure | log metric filter |
| Broker disconnected | log metric filter |
| Strategy crashed | log metric filter |
| Database unavailable | log metric filter |

**Health.** A local HTTP endpoint on 127.0.0.1 (not exposed — the security group has no inbound rules) reporting session state, connection health, staleness, and last reconcile, for SSM-session debugging.

---

## 15. Control plane and Next.js dashboard

The dashboard is the primary interface. It is a **read-mostly view plus a command emitter** — never an actor.

### 15.1 Command bus

```
Dashboard click
      │ POST /api/commands  {type, params, idempotency_key}
      ▼
FastAPI control service ── validates shape + authorisation
      │                     (no broker code, no broker credentials)
      ▼
`commands` collection  status=pending
      │ change stream (or 1s poll fallback)
      ▼
Worker CommandProcessor ── revalidates against live state
      │
      ├─ order-affecting? ──▶ Risk Engine ──▶ Execution Engine ──▶ Broker
      └─ otherwise        ──▶ session / config / job subsystem
      │
      ▼
status: accepted → executing → done | failed | rejected(reason)
      │
      ▼
Dashboard polls/streams status — UI shows real state, never optimistic success
```

**Command catalogue.**

| Command | Path | Notes |
|---|---|---|
| `SET_KILL_SWITCH` | direct flag write | Highest priority; also available via CLI and SSM — must never depend on the dashboard |
| `SQUARE_OFF_ALL` / `CLOSE_POSITION` | **through risk + execution** | Emits normal limit orders with repricing |
| `CANCEL_ORDER` | through execution | Idempotent; no-op if already terminal |
| `PLACE_MANUAL_ORDER` | **through risk + execution** | Tagged `source=MANUAL`; obeys every risk rule exactly as a strategy signal does |
| `START_STRATEGY` / `STOP_STRATEGY` | session subsystem | Stop is graceful; positions are not auto-closed unless requested |
| `UPDATE_STRATEGY_CONFIG` | config subsystem | Schema-validated; snapshotted to `strategy_runs`; rejected mid-session unless explicitly forced |
| `TRIGGER_BACKFILL` / `RUN_BACKTEST` | job subsystem | Long-running; progress reported via `command_results` |
| `RECONCILE_NOW` | reconciler | Safe to invoke any time |
| `SET_TRADING_MODE` | **rejected at runtime** | Paper↔live is a deployment action, not a UI toggle (§20) |

Every command carries a client-generated `idempotency_key` with a unique index, so a double-click, a refresh, or a network retry cannot execute twice — the same guarantee as order placement (Decision 7), for the same reason.

### 15.2 Dashboard surface

**Hosted on Vercel, deployed by the operator** from the `dashboard/` directory of this repo (Vercel root directory setting). Next.js App Router, TypeScript, Tailwind + shadcn/ui, TanStack Query for caching, lightweight-charts for candles. Because the UI no longer shares the 2 GB box, SSR and server components are available — but note that any server-side fetch still goes to `empapi.journeymen.in`, never to Atlas (Decision 13).

API client types are generated from FastAPI's OpenAPI schema, so a backend contract change breaks the frontend build rather than production.

| Route | Shows |
|---|---|
| `/` overview | Session state, broker/feed health, staleness, day P&L, open positions, kill switch |
| `/positions` | Live positions, unrealised P&L, per-position close |
| `/orders` | Order book with full state machine history from `order_events`; cancel |
| `/strategies` | Per-strategy status, P&L, signals, start/stop, config editor with diff preview |
| `/risk` | Limit utilisation gauges, `risk_events` feed with rejection reasons |
| `/market` | Watchlist, live quotes, candle charts |
| `/backtests` | Launch runs, compare metrics, equity/drawdown curves, trade list |
| `/system` | Health, reconciliation history, `system_events`, command audit log |

**Live updates:** MongoDB change streams → FastAPI **SSE** → TanStack Query cache. SSE, not WebSocket, because the flow is one-directional and SSE reconnects automatically. The browser opens the SSE stream **directly against `empapi.journeymen.in`**, not through Vercel, so there is no serverless function holding a long-lived connection (which Vercel would time out anyway). Polling at 2s is the fallback. *Note:* change streams need a replica set — Atlas provides one; a self-hosted mongod (Decision 5) **must be initialised as a single-node replica set**, which is required for our fill transactions anyway.

**Explicit non-goals — enforced, not just documented:** the dashboard holds no broker credentials, contains no strategy logic, computes no P&L (it displays what the worker computed), and makes no trading decision. An import-linter rule and a CI check assert that nothing under `api/` or `dashboard/` imports broker code.

### 15.3 Authentication — deliberately minimal

This is a **single-operator, entirely private system**, so the auth design is kept small on purpose:

1. A **single passcode**, stored in MongoDB as an Argon2id hash (never plaintext, never in Git or env).
2. `POST /auth/login` with the passcode → the API issues a **signed JWT** (HS256, signing secret in SSM Parameter Store).
3. The browser sends `Authorization: Bearer <jwt>` on every request; the API validates signature and `exp`.
4. Token lifetime ~12 hours, comfortably covering a trading day, with re-login on expiry.

No OAuth provider, no user table, no roles, no registration, no TOTP step-up. There is one user and one secret.

Three small guards are kept, because they cost almost nothing and the endpoint is public: **rate-limit the login route** (a public passcode endpoint is brute-forceable otherwise), **compare in constant time**, and **alarm on repeated auth failures**. Passcode rotation is a documented runbook step — one hash update, no migration.

### 15.4 CLI is retained deliberately

The CLI stops being the primary interface but is **not** removed. It remains the path for CI, scripted backfills, migrations, and — critically — emergency control when the dashboard, Cloudflare, or the API is unavailable. `emporos halt` over SSM Session Manager must always work. A safety control with a single failure-prone path is not a safety control.

---

## 16. Security

- **Secrets** in SSM Parameter Store `SecureString`, read at startup via the EC2 instance role (Decision 10). Never in Git, AMIs, or committed env files. `.env.example` lists names only. **The dashboard and API receive no broker secrets at all** — only a read-only Mongo URI and a command-write URI.
- **Inbound is 443 only**, for `empapi.journeymen.in` (Decision 13). Port 80 stays closed — Caddy uses the TLS-ALPN-01 challenge. Shell access remains **SSM Session Manager** only; no SSH keys exist, so no SSH key can leak.
- **App-layer auth is the entire perimeter** (§15.3), because Vercel's dynamic egress makes network filtering impossible. Single passcode (Argon2id hash in MongoDB) exchanged for a signed JWT; every request validates signature and expiry. Deliberately minimal for a private single-operator system — the compensating controls are a **rate-limited login route**, constant-time comparison, and an alarm on repeated auth failures.
- **The API binds to localhost**; Caddy is the only thing listening publicly and proxies to it.
- **Blast-radius containment.** Caddy and the API run with explicit CPU and memory limits; the worker holds reserved CPU shares. Degrading the public endpoint must never degrade trading. Rate limiting at Caddy plus per-identity limits in the API.
- **CORS is restricted to the single production dashboard origin.** Vercel preview deployments have random URLs and are **not** permitted against the production API — previews point at a staging API or nothing.
- **Atlas stays allowlisted to the Elastic IP only.** No `0.0.0.0/0`. This is the direct payoff of routing all dashboard reads through the API.
- **Security headers** (HSTS, CSP, X-Content-Type-Options, frame-ancestors none) set at Caddy; request body size limits enforced.
- **Abuse alarms:** CloudWatch alarms on 4xx/5xx spikes, repeated auth failures, and unusual request volume — a public endpoint on a trading box needs to be watched, not merely hardened.
- **Destructive commands** (`SQUARE_OFF_ALL`, `PLACE_MANUAL_ORDER`, `SET_TRADING_MODE`) require a typed confirmation in the UI and are recorded in the command audit log.
- **Egress** restricted to HTTPS 443.
- **IAM least privilege:** the instance role may read only its own `/emporos/*` parameters, write its own S3 prefix, and put its own CloudWatch metrics/logs. A separate deploy role for CI with no runtime permissions.
- **Atlas:** IP allowlist limited to the Elastic IP. A dedicated database user scoped to the `emporos` database. A separate read-only user for the future dashboard. TLS enforced. **[Note: PrivateLink requires M10+ and is out of scope at this budget — the IP allowlist plus SCRAM plus TLS is the available control.]**
- **Audit:** CloudTrail on Parameter Store reads; `system_events` and `order_events` as the immutable application audit trail.
- **Supply chain:** `Pipfile.lock` hashes verified with `pipenv install --deploy`; `pip-audit` and `bandit` in CI; Dependabot on GitHub Actions.

---

## 17. CI/CD and Pipenv packaging

**Pipenv is the single source of truth.** `Pipfile` + committed `Pipfile.lock`.

```
PR → ruff (lint+format) → mypy --strict on domain/broker/risk/execution
   → import-linter (architecture boundaries, incl. api/ and dashboard/
                    importing no broker code)
   → unit tests → integration tests (real Atlas `emporos_dev` via MONGO_URL, §6.0 —
                    a GitHub Actions secret, no local/CI Mongo container)
   → contract tests → backtest regression (golden files)
   → dashboard CI only: tsc --noEmit, eslint, generated API client matches
                OpenAPI schema (fails on backend contract drift), next build,
                Playwright E2E  — deployment itself is Vercel's git integration,
                driven by the operator, not this pipeline
   → bandit + pip-audit + npm audit
   → build arm64 image (worker + api + caddy config) → push to ECR (:sha)

main → deploy staging (paper mode) → smoke test
     → MANUAL APPROVAL → deploy production
```

**Packaging.** Multi-stage `Dockerfile` for `linux/arm64`:

```dockerfile
FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir pipenv
COPY Pipfile Pipfile.lock ./
# --deploy fails if the lock is out of sync with the Pipfile — determinism gate
RUN pipenv install --deploy --system --ignore-pipfile
FROM python:3.12-slim
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY src/ /app/src/
```

No `requirements.txt` in the dependency path. (`pipenv requirements` may generate one transiently for a tool that demands the format, but it is never committed and never the source of truth.) Because we deploy no Lambda, the Lambda packaging problem does not arise; if a Lambda is ever added at Phase 19, the same `pipenv requirements` → `pip install -t` route into a zip or container image keeps Pipenv authoritative.

**Deployment** is via SSM Run Command: pull the new image tag, restart the systemd unit, health-check, and roll back to the previous tag on failure. No SSH, no inbound ports.

**Hard deploy guard:** deployment to production is blocked while the market is open unless explicitly forced. Restarting the worker mid-session is a trading risk, not just an availability one.

---

## 18. Testing strategy

| Layer | Scope | Tooling |
|---|---|---|
| **Unit** | indicators, cost model, position sizing, P&L, risk rules, order state machine, binary tick parser | pytest, `freezegun`, hypothesis for money/quantity invariants |
| **Integration** | repositories, TTL/unique index behaviour, transactions, instrument sync, historical backfill | **real MongoDB** — the Atlas `emporos_dev` database via `MONGO_URL` (§6.0), never mocked; `mongomock` diverges exactly where we depend on real semantics (unique indexes, time-series restrictions) |
| **Contract** | every `Broker` implementation against one shared suite; recorded SmartAPI fixtures; cross-check request shapes and binary parsing against the pinned SDK as an oracle | pytest, `respx` |
| **Regression** | golden-file backtests over a fixed dataset; any metric drift fails the build | pytest + committed golden JSON |
| **Failure** | duplicate placement, ambiguous response → `UNKNOWN` → resolution, WS disconnect mid-order, DB outage, stale data, partial fill, rejection, restart with open orders, redelivered fills | pytest with fault-injecting fakes |
| **E2E (dashboard)** | kill switch, strategy start/stop, order cancel, manual order rejection, command idempotency under double-submit, unauthenticated access refused | Playwright against a paper worker |
| **Paper** | full live pipeline against live data, simulated execution, zero capital | manual + scripted multi-day soak |

Coverage gates: `risk/`, `execution/`, `domain/` ≥ 90%. Elsewhere ≥ 70%. The gate is high precisely where a bug costs money.

---

## 19. AWS infrastructure (Terraform)

```
VPC 10.0.0.0/16
└── public subnet 10.0.1.0/24 (ap-south-1a)
    ├── Internet Gateway
    ├── EC2 t4g.small, ARM64, IMDSv2 required
    │   ├── root 20 GB gp3 + data 10 GB gp3 [delete_on_termination = false]
    │   ├── Elastic IP  ← registered with Angel One [prevent_destroy]
    │   │                 ← also the A record target for empapi.journeymen.in
    │   ├── SG: inbound 443 only (0.0.0.0/0), outbound 443. Port 80 closed.
    │   ├── containers: worker (reserved CPU) · control-api · caddy (capped)
    │   └── IAM instance role: SSM Core, ssm:GetParameter /emporos/*,
    │                          s3 rw own prefix, cloudwatch put
    ├── S3: emporos-data (candles, ticks, backtests, reports) — versioned,
    │       lifecycle → IA at 90d, Glacier IR at 365d
    ├── S3: emporos-tfstate — versioned, native locking
    ├── SSM Parameter Store: /emporos/prod/*  (SecureString)
    ├── CloudWatch: log group (30d), 8 custom metrics, ~10 alarms
    ├── SNS topic → email
    └── ECR: emporos-worker (lifecycle: keep last 10 images)
```

Outside AWS: the **Next.js dashboard on Vercel** (operator-deployed) and a DNS A record `empapi.journeymen.in → Elastic IP` on the existing `journeymen.in` domain.

No NAT Gateway. No load balancer. No API Gateway. No Lambda. No DynamoDB. No VPC endpoints (the instance reaches SSM over the internet gateway via its public IP). Caddy provides TLS termination for ~$0, which is the whole reason no ALB appears here.

> **Note the posture change:** this is the one place the architecture accepts a public listener on the trading box. It is a deliberate trade for not migrating DNS (Decision 13). If it ever proves troublesome, moving `journeymen.in` to Cloudflare and switching to a tunnel restores zero-inbound without touching application code.

Staging is the **same Terraform module with `count = var.enable_staging`**, normally 0. Staging runs by temporarily starting an instance in paper mode, not by paying for one continuously.

---

## 20. Cost estimate (ap-south-1, USD/month) **[VOLATILE]**

| Item | Dev | **Build phases 1–18 (M0)** | Prod: Flex | Prod: self-hosted DB | Serverless-heavy (rejected) |
|---|---|---|---|---|---|
| EC2 t4g.small | — (local) | 9.80 | 9.80 | 9.80 | — |
| Fargate 0.5vCPU/1GB 24×5 | — | — | — | — | ~13.00 |
| **NAT Gateway** | — | **0.00** | **0.00** | **0.00** | **32.40 + data** |
| Public IPv4 (EIP) | — | 3.65 | 3.65 | 3.65 | 3.65 |
| EBS gp3 | — | 1.92 (20 GB) | 1.92 (20 GB) | 5.47 (60 GB) | — |
| EBS snapshots (DLM) | — | — | — | 1.00 | — |
| S3 (~10 GB + requests) | 0.30 | 0.50 | 0.50 | 0.50 | 0.50 |
| CloudWatch (8 metrics, logs, alarms) | — | 3.50 | 3.50 | 3.50 | 5.00 |
| SNS email | — | ~0.00 | ~0.00 | ~0.00 | ~0.00 |
| ECR | — | 0.10 | 0.10 | 0.10 | 0.10 |
| Secrets (SSM Standard) | — | **0.00** | **0.00** | **0.00** | 0.00 |
| Lambda + API Gateway | — | 0.00 | 0.00 | 0.00 | ~1.00 |
| Dashboard hosting (Vercel Hobby) | — | **0.00** | **0.00** | **0.00** | — |
| Domain (`journeymen.in`, already owned) | — | **0.00** | **0.00** | **0.00** | 0.00 |
| TLS (Caddy + Let's Encrypt) | — | **0.00** | **0.00** | **0.00** | — |
| MongoDB | 0.00 (M0) | **0.00 (M0)** | 10.00 (Flex) | **0.00 (on-box)** | 10.00 |
| **Total** | **~0.30** | **≈ 19.47** | ≈ 29.47 | ≈ 24.02 | ≈ 65.65 + NAT data |

**Against the ~$10–25 target.** The build phases land at **~$19.50**, inside the ceiling — and the entire Next.js dashboard and control plane adds **$0**, because Vercel Hobby is free, the domain is already owned, and Caddy handles TLS for nothing. Production lands at ~$29.50 on Flex or ~$24 self-hosted; a **1-year Compute Savings Plan** on the EC2 (−~$3) brings those to ~$26.50 and ~$21, and trimming CloudWatch custom metrics from 8 to 4 saves a further ~$1.20.

**Realistic trajectory: ~$16.50/mo (M0 + Savings Plan) through paper trading, then ~$21–26.50/mo once live**, depending on the Phase 19 hosting decision. Both live figures sit at or just above the stated ceiling — and the delta buys either managed backups (Flex) or all-hot history with no S3 dependency (self-hosted). Note the self-hosted column is only ~$5.50 cheaper than Flex and assumes 2 GB RAM suffices; on a t4g.medium it rises to ~$26 and the saving disappears entirely.

**The headline finding is unchanged by any of this:** the serverless-heavy variant costs **more than double** the recommended architecture, and its single largest line item is the NAT Gateway — a component that exists purely to give Lambda the static IP that EC2 has natively for $3.65. That is the concrete validation of Decisions 1–3.

---

## 21. Work tracking — Jira is mandatory

**All work is tracked in Jira. No code is written for untracked work.**

| Setting | Value |
|---|---|
| Site | `journeymenin.atlassian.net` |
| Cloud ID | `672756d0-9631-4d62-9c11-9a21de77410e` |
| Project | **EM — Emporos** (id `10067`, next-gen software) |
| Issue types | Epic (`10081`), Story (`10082`), Task (`10084`), Bug (`10083`), Feature (`10085`), Subtask (`10080`) |
| Access | Atlassian MCP, verified connected with `read:jira-work` + `write:jira-work` |

**If the Atlassian MCP is unavailable, stop and ask the operator to reconnect it before doing anything else.** Do not proceed with untracked work and do not keep a local substitute backlog.

### Workflow — every item, without exception

```
   File in To Do  ──▶  move to In Progress  ──▶  move to Done
   (before work)       (when work starts)        (once committed)
```

1. **Before starting**, create the issue in **To Do**. Every feature, bug and task gets filed first — including ones discovered mid-work.
2. **Transition to In Progress** when work actually begins, not when it is planned.
3. **Transition to Done** only once the change is **committed**. Not when it compiles, not when it is written — committed.

### Structure

- One **Epic per phase** (Phase 0 → Phase 21), titled e.g. `Phase 5 — Market data`.
- Each phase's implementation tasks become **Stories or Tasks** under that Epic, drawn from the phase's task list in §22.
- Each phase's tests become their own tracked items where they are substantial (the Phase 12 failure suite, for example, is not a checkbox on another ticket).
- **Bugs** are filed as `Bug` even when found and fixed in the same sitting — the record matters more than the ceremony.
- The **acceptance criteria** in each phase below become the issue's acceptance criteria verbatim, so "Done" has a fixed meaning.

### Commit convention

Reference the issue key in every commit so Jira and git stay correlated:

```
EM-123: add idempotency key unique index to orders
```

No trailers, no co-author lines, no generated-with attribution — the issue key is the only required element.

### First action on approval

Create the Phase 0–2 Epics and their child issues in **To Do** before writing any code, then move `Phase 1` items to **In Progress** as they are picked up. Later phases are filed as their predecessors approach completion, so the backlog reflects real intent rather than a wall of speculative tickets.

---

## 22. Implementation roadmap

Each phase is independently executable and leaves the system in a working state.

### Phase 0 — Research & architecture (this document)
**Deliverable:** `plan.md` plus the 17 documents in `docs/` derived from it (system architecture, AWS architecture, data model, broker integration spec, strategy framework, backtesting spec, risk spec, execution spec, security model, CI/CD plan, testing strategy, cost analysis, DR plan, roadmap, runbook, repo structure, task breakdown).
**Acceptance:** every major decision records reason, alternatives, rejection rationale, and cost.

### Phase 1 — Bootstrap
Pipfile with pinned deps; `src/` layout; ruff/mypy/pytest/import-linter config; `core/` (config loader with env overlays, clock, ULID, typed errors, event bus, and the `ENV`→database resolution from §6.0); structured logging; typer CLI skeleton; CI pipeline green on an empty test suite.
**Acceptance:** `pipenv install --deploy` succeeds; `emporos --help` runs; CI green; import-linter enforces `domain` importing nothing.
**Rollback:** delete branch.

### Phase 2 — Persistence
Mongo client with documented pool settings (connection URI, write concern and pool size all **configuration, not literals** — Decision 5 may move this to a single-node on-box deployment); repository pattern per collection; migration runner creating all indexes (**unique indexes are the deliverable that matters**); `Decimal128` money handling; S3 store abstraction; **`CandleRepository` owning the hot/cold union** so no caller ever queries the `candles` collection directly.
**Tests:** integration against real Mongo — unique index violation raises the expected typed error; TTL configured; transaction helper works; `CandleRepository` returns an identical bar series whether a range is served entirely from Mongo, entirely from S3, or spans both.
**Acceptance:** `emporos db migrate` is idempotent; every index in §6 exists; the whole suite passes against the Atlas `emporos_dev` database via `MONGO_URL` (§6.0) — the same client code path production uses against `emporos`, differing only in the resolved database name. This is what keeps the Phase 19 hosting decision cheap.
**Rollback:** migrations are additive and reversible.

### Phase 3 — Instrument master
Downloader, validator (row-count and field sanity gate), differ, version writer, atomic swap, in-memory resolver cache, CLI `emporos instruments sync`.
**Tests:** malformed/truncated file does **not** wipe the master; version records created on change; token→instrument resolution.
**Acceptance:** NSE/BSE cash instruments load; a second sync is a no-op; corrupt input is rejected with an alert.
**Risk:** upstream URL or schema changes → validator catches it and keeps yesterday's data.

### Phase 4 — Angel One authentication & REST transport
**Prereq: SmartAPI onboarding** — create the app under the new static-IP login at `smartapi.angelone.in`, enrol TOTP, register the Elastic IP (requires Phase 15's EIP, so allocate that EIP early or use a placeholder and update once — remembering the once-per-week change limit).
Own `httpx` transport; typed errors classified as retryable / definitive / **ambiguous**; per-endpoint-group token-bucket rate limiter; exponential backoff with jitter tuned for the known false-positive rate-limit defect; TOTP login; session store; daily re-auth; feed token handling.
**Tests:** contract tests against recorded fixtures; rate limiter respects per-second *and* per-minute caps; ambiguous errors classified correctly (this classification is what Phase 12 depends on); TLS verification **enabled**.
**Acceptance:** authenticate against a real account, fetch profile and funds, observe correct backoff under induced 429s.
**Risk:** SmartAPI response shapes differ from docs → fixtures recorded from the live account are authoritative.

### Phase 5 — Market data
SmartWebSocketV2 client (own implementation, TLS verified), binary parser, heartbeat, reconnect with backoff, subscription manager (≤1000 tokens, 1 connection), normalizer, dedupe/out-of-order handling, 1m aggregator with wall-clock bar close, higher timeframes from closed bars, staleness watchdog, idempotent candle upsert.
**Tests:** binary parser against recorded frames and the SDK oracle; duplicate/out-of-order ticks; bar closes on time for a silent instrument; reconnect resubscribes and backfills; upsert is idempotent.
**Acceptance:** 1m candles for a live watchlist match broker candles within tolerance across a full session.

### Phase 6 — Historical data
Backfill orchestrator with resumable checkpoints; gap detection against the market calendar; idempotent upsert; S3 Parquet archive; hot/cold union loader; nightly rollup job.
**Tests:** resume after interruption; gaps detected and filled; re-run produces no duplicates; spurious rate-limit errors survived via backoff.
**Acceptance:** backfill 1 year of 1m candles for the watchlist with zero gaps and zero duplicates.
**Risk:** the `getCandleData` defect (§1.4) makes this slow — budget wall-clock time and make it resumable.

### Phase 7 — Broker abstraction
`Broker` ABC, broker-neutral DTOs, `AngelOneBroker` adapter, `mapping.py`, shared contract test suite.
**Acceptance:** contract suite passes against `AngelOneBroker`; nothing outside `broker/angelone/` imports SmartAPI concepts (import-linter enforced).

### Phase 8 — Paper broker
`PaperBroker` on live market data with simulated execution, realistic limit-fill logic, configurable rejects and partials, full persistence of simulated signals/orders/fills/positions/P&L.
**Acceptance:** same contract suite passes; a strategy runs end-to-end on live data with zero capital at risk.

### Phase 9 — Strategy framework
`Strategy` ABC, `StrategyContext` (no broker handle), registry, YAML config loader with schema validation, config snapshotting, indicator library, one reference strategy.
**Tests:** determinism (identical input → identical signals); a strategy cannot reach a broker; config snapshot reproduces a run.
**Acceptance:** reference strategy emits signals from historical bars deterministically.

### Phase 10 — Backtesting
Engine, `SimulatedBroker`, look-ahead-proof feed, cost model from dated YAML, fill model, portfolio accounting, full metrics suite, walk-forward, golden-file regression.
**Tests:** a deliberately look-ahead-biased strategy is structurally impossible to express; cost model verified against hand-computed contract notes; metrics verified against known series.
**Acceptance:** reference strategy backtests over 1 year with a full metrics report; golden files committed.

### Phase 11 — Risk engine
All rules from §11 as individual testable classes; ordered evaluation; `risk_events` persistence; kill switch (Mongo flag + file sentinel + CLI).
**Tests:** every rule has explicit allow/block cases; ordering verified; kill switch halts within one poll interval.
**Acceptance:** ≥90% coverage on `risk/`; no order path bypasses the engine (enforced by test and by `execution` accepting only `RiskApprovedSignal`).

### Phase 12 — Execution engine
State machine, `order_events` audit log, idempotency protocol (Decision 7), `UNKNOWN` resolution via `find_orders_by_tag`, repricing policy, OPS budget, idempotent fill processing.
**Tests (the most important in the project):** ambiguous response → `UNKNOWN` → resolution adopts the existing broker order and places **no** duplicate; crash between intent-write and broker call recovers without duplication; redelivered fills are idempotent; reprice chain bounded; **no code path can emit a MARKET or IOC order**.
**Acceptance:** failure suite green; a chaos run with injected timeouts produces zero duplicate orders.
**Rollback:** `LIVE_TRADING_ENABLED=false` renders the whole subsystem inert.

### Phase 13 — Portfolio & reconciliation
Position tracking, realised/unrealised P&L, snapshots, reconciler with the full discrepancy taxonomy, auto-heal boundaries, halt-on-ambiguity.
**Tests:** each discrepancy type detected; manual (app-placed) orders discovered; auto-heal limited to safe cases.
**Acceptance:** reconciliation runs clean against a real paper session and detects a deliberately injected mismatch.

### Phase 14 — Observability
Correlation-ID propagation, CloudWatch metrics, log metric filters, alarms, SNS, health endpoint, EOD report.
**Acceptance:** a single signal traced end-to-end by correlation ID; killing the worker fires the dead-man's-switch alarm to email.

### Phase 15 — Control plane (command bus + API)
`commands`/`command_results` collections with unique idempotency index; command models and schema validation; worker-side `CommandProcessor` consuming via change stream with a 1s poll fallback; **order-affecting commands routed through the existing risk and execution engines, not around them**; FastAPI control service with read-only Mongo access for queries and command-write access for mutations; OpenAPI schema published for client generation; SSE endpoint backed by change streams.
**Tests:** a command is never executed twice (double-submit, replay, worker restart mid-execution); `PLACE_MANUAL_ORDER` is rejected by every risk rule that would reject an equivalent strategy signal; `SET_TRADING_MODE` is refused at runtime; commands issued while the worker is down execute on recovery or expire, never silently vanish; the API package imports no broker code (import-linter).
**Acceptance:** every command in §15.1 works end-to-end via HTTP with correct status transitions; contract test proves the API cannot reach Angel One.
**Rollback:** the worker runs normally with the command processor disabled — the control plane is additive.

### Phase 16 — Next.js dashboard on Vercel
App Router + TypeScript + Tailwind + shadcn/ui in `dashboard/`; typed API client generated from the OpenAPI schema; all eight routes from §15.2; SSE live updates (browser → `empapi` directly) with polling fallback; **single-passcode login exchanged for a JWT** (§15.3); typed confirmation on destructive commands; command status surfaced honestly (never optimistic success).
Server side: Caddy container with TLS-ALPN-01, rate limiting, security headers and CORS locked to the production origin; API bound to localhost; CPU/memory caps on Caddy and API with reserved shares for the worker.
**Operator tasks (not CI):** create the A record `empapi.journeymen.in → Elastic IP`; connect the Vercel project with root directory `dashboard/`; set the API base URL in Vercel env; set the passcode hash and JWT signing secret in SSM.
**Tests:** component tests on state rendering; Playwright E2E driving kill switch, strategy start/stop and order cancel against a paper worker; build fails when the backend contract changes; **API rejects a request with a missing, expired, or wrong-signature token**; login route rate-limits under repeated wrong passcodes; CORS rejects a non-production origin; verified no broker credential or broker call exists anywhere in the bundle.
**Acceptance:** the full paper-trading workflow is operable from the browser with no terminal; TLS valid and auto-renewing; only 443 open; **a load test against the public endpoint leaves worker tick-processing latency unaffected** — this is the acceptance criterion that proves the blast-radius containment actually works.
**Risk:** the public listener is the main new attack surface; auth, rate limiting and resource caps are all load-bearing and must be verified, not assumed.

### Phase 17 — AWS infrastructure
Terraform for everything in §18; EIP with `prevent_destroy`; instance role; SSM parameters; ECR; deploy via SSM Run Command; AMI rebuild runbook.
Also: the Caddy container and its config, the inbound-443 security group rule, and the JWT signing secret in SSM (Decision 13). DNS for `empapi.journeymen.in` is managed by the operator at the registrar, outside Terraform.
Provision the data directory as a **separate EBS volume with `delete_on_termination = false`**, mounted at `/var/lib/emporos-data`, even while on Atlas. It costs ~$1/mo for 10 GB, holds logs and the S3 staging cache meanwhile, and means adopting self-hosted MongoDB at Phase 19 is a mount-and-configure change rather than a re-architecture (Decision 5).
**Acceptance:** `terraform apply` from zero produces a running worker; documented rebuild completes in under 30 minutes; EIP survives a destroy/apply of everything else; the data volume survives instance termination and re-attaches cleanly.
**Risk:** EIP registered with Angel One — changing it is limited to once per calendar week. Treat as protected.

### Phase 18 — Paper trading soak
Run continuously in paper mode for a **minimum of 20 trading sessions**. Track reconnects, staleness, reconciliation mismatches, unknown orders, strategy errors.
Also capture the inputs to the Phase 19 hosting decision: peak RSS across all containers (worker, API, Caddy, cloudflared), CPU credit balance across a full session, measured candle growth per session, and actual Mongo storage consumed. These measurements are the deliverable that makes Decision 5 evidence-based rather than a guess.
**Acceptance:** 20 sessions with zero unresolved unknown orders, zero unexplained reconciliation mismatches, and no unhandled exceptions. This gate is non-negotiable before live.

### Phase 19 — Production hardening (includes the database hosting decision)
Close every gap the soak exposed; DR drill (rebuild from scratch and reconcile); runbook; 1-year Compute Savings Plan.

**Make the Decision 5 call here, using Phase 16's measurements.** Choose **self-hosted MongoDB on the EC2 data volume** only if all four hold: worker RSS under load leaves ≥ 1 GB headroom on t4g.small; CPU credit balance stayed healthy across full paper sessions; nightly `mongodump` → S3 with a **tested restore** is in place; the data volume is on daily DLM snapshots. Otherwise **upgrade M0 → Atlas Flex** and keep the failure domains separate. Record the decision and its evidence in `docs/`.

If self-hosting is chosen: pin WiredTiger cache (~512 MB), configure swap, confirm mongod is `nice`d below the trading process, and verify the OOM score adjustment protects the worker. If Flex is chosen: confirm the S3 rollup job is running and the Flex storage cap is not approaching.

**Acceptance:** production-readiness checklist (§21) fully green; DR drill performed and timed; database hosting decided with evidence, and a restore from backup demonstrated on whichever option was chosen.

### Phase 20 — Limited live trading
`LIVE_TRADING_ENABLED=true` via a deliberate, approved deployment. Start with **one strategy, one instrument, minimum quantity, tight daily loss limit**. Scale only after a documented review at each step.
**Acceptance:** first live order placed, filled, reconciled, and correctly reflected in P&L.
**Rollback:** kill switch, then `LIVE_TRADING_ENABLED=false`, then redeploy previous image tag.

### Phase 21 — Advanced strategies / ML (optional)
Research environment separate from execution. `SignalModel` interface with `RuleBasedSignalModel` and `MLSignalModel` coexisting. Temporal CV, leakage detection, model versioning. ML never enters the execution path — it produces signals that pass through the identical risk and execution layers.

---

## 23. Production-readiness checklist

**Reliability** — systemd restart verified · reconnect tested under real drops · DB outage degrades safely · 20-session paper soak clean · DR rebuild timed

**Correctness** — golden backtest regression green · cost model verified against real contract notes · P&L matches broker statement for a full paper month · no look-ahead demonstrable · order state machine exhaustively tested

**Security** — zero secrets in Git (history scanned) · SSM SecureString + instance role · **inbound 443 only, port 80 closed** · no SSH keys · Atlas allowlisted to the EIP (never `0.0.0.0/0`) · TLS verification enabled everywhere · TLS cert auto-renewal verified · IMDSv2 required · `pip-audit`/`bandit`/`npm audit` green · passcode stored only as an Argon2id hash · **JWT rejected when missing, expired, or wrongly signed** · login route rate-limited and constant-time · CORS rejects non-production origins · **dashboard bundle contains no broker credential or broker call** · abuse alarms firing on 4xx/5xx and auth-failure spikes

**Control plane** — every command idempotent under double-submit and replay · order-affecting commands proven to traverse the risk engine · `PLACE_MANUAL_ORDER` rejected by the same rules as a strategy signal · command audit log records authenticated identity · **kill switch verified working with the dashboard down** · destructive actions require typed confirmation

**Observability** — every alarm tested by inducing the condition · correlation IDs traceable end-to-end · dead-man's switch verified by killing the worker · EOD report delivered

**Risk** — every rule unit-tested · kill switch tested live · daily loss limit tested by simulated breach · `LIVE_TRADING_ENABLED` defaults false · risk cannot be bypassed

**Reconciliation** — clean across 20 paper sessions · injected mismatch detected · manual app orders detected · unknown-order resolution proven duplicate-free

**Data** — unique indexes present and enforced · idempotent candle ingestion proven · no gaps in a year of history · retention/TTL verified · S3 rollup verified · database hosting decided with evidence (Decision 5) · **backup restore actually performed**, not merely configured · `CandleRepository` verified against both hot-only and spanning ranges

**Deployment** — full `terraform apply` from zero · rollback to previous image tested · deploy blocked during market hours · `Pipfile.lock` deterministic (`--deploy` gate)

**Compliance** — static IP registered and matching the EIP · no market or IOC orders possible · order tagging in place · OPS budget below threshold · kill switch present · tech-savvy-client status confirmed with Angel One

**Cost** — billing alarm configured · monthly spend verified against the §19 estimate

**Operations** — runbook covers start/stop/halt/square-off/recover/rotate-secrets/rotate-passcode/change-IP · first-live-trade procedure documented · escalation path defined · **every shipped change traceable to a Done Jira issue** (§21)

---

## 24. Verification

How to confirm the system works, end to end, without risking capital:

1. `pipenv install --deploy && pipenv run pytest` — full suite including failure tests, against the real Atlas `emporos_dev` database via `MONGO_URL`.
2. `emporos db migrate && emporos instruments sync` — confirm the instrument master loads and indexes exist.
3. `emporos backfill --days 30 --watchlist config/watchlist.yaml` — confirm zero gaps and zero duplicates on re-run.
4. `emporos backtest --strategy momentum_v1 --from 2025-09-01 --to 2026-09-01` — compare against the committed golden file.
5. `emporos run --mode paper` during a live session — watch candles form, signals fire, risk decisions log, simulated fills reconcile.
6. Open the dashboard and drive a full paper session from the browser alone: start a strategy, watch positions update live over SSE, cancel an order, place a manual order and confirm it is risk-checked, hit the kill switch. Confirm command statuses reflect reality rather than optimistic success, and that double-clicking a destructive action executes once.
7. Confirm the security posture holds: only 443 is open and port 80 is closed; `empapi.journeymen.in` serves a valid auto-renewing certificate; an unauthenticated request is refused, as is one with an expired or tampered token; repeated wrong passcodes trip the rate limiter; a request from an unapproved origin fails CORS; `emporos halt` still works over SSM with the dashboard and API stopped.
8. Load-test the public endpoint while a paper session runs and confirm **worker tick-processing latency is unaffected** — the resource caps are only real if measured.
9. Induce failures deliberately: kill the process mid-session, drop the network, set the kill switch, inject a reconciliation mismatch, submit a command while the worker is down. Confirm recovery, alarms, and — critically — **zero duplicate orders**.
10. Only after 20 clean paper sessions, proceed to Phase 20.

---

## 25. Things that will change — re-verify before implementing

- **Rate limits** (§1.4) and the `getCandleData` defect status — check the SmartAPI forum at implementation time.
- **Static IP flow** (§1.3): the legacy key flow was still supported "temporarily" with no stated deadline; confirm the current state and the family-sharing policy.
- **Order rate**: documented 20/sec vs the 9/sec stated in the static-IP rollout post. Confirm before setting the OPS budget.
- **`ordertag` length limit** — the idempotency encoding depends on it.
- **Order varieties and product types** accepted for cash intraday post-April-2026, given the market/IOC ban.
- **Fee schedule** (§10) — every rate; these change and a stale schedule silently corrupts backtests.
- **AWS and Atlas pricing** (§19) — verify against the calculator.
- **SDK status** — if `smartapi-python` revives, revisit Decision 4 (the decision holds regardless while it remains unmaintained).
- **NSE holiday calendar** — reseed annually.

### Key sources

- [SmartAPI documentation](https://smartapi.angelbroking.com/docs) · [Orders](https://smartapi.angelbroking.com/docs/Orders) · [User](https://smartapi.angelbroking.com/docs/User)
- [What's Changing in Angel One's SmartAPI Access from April 1, 2026](https://www.angelone.in/news/market-updates/what-s-changing-in-angel-one-s-smartapi-access-from-april-1-2026)
- [Static IP based API keys now live](https://smartapi.angelone.in/smartapi/forum/topic/5352/static-ip-based-api-keys-now-live-old-flow-still-supported-temporarily)
- [Changes in API Rate Limit](https://smartapi.angelone.in/smartapi/forum/topic/4387/changes-in-api-rate-limit)
- [getCandleData rate-limit false positives](https://smartapi.angelone.in/smartapi/forum/topic/5639/getcandledata-rate-limit-false-positives-at-0-003-req-sec-six-independent-reports-zero-acknowledgment)
- [WebSocket streaming size and max connections](https://smartapi.angelone.in/smartapi/forum/topic/4391/websocket-streaming-size-and-max-connections)
- [Real-time order updates via WebSocket](https://smartapi.angelone.in/smartapi/forum/topic/4041/new-real-time-order-updates-via-websocket-from-tns-angelone-in-smart-order-update)
- [angel-one/smartapi-python](https://github.com/angel-one/smartapi-python) · [smartConnect.py](https://github.com/angel-one/smartapi-python/blob/main/SmartApi/smartConnect.py) · [smartWebSocketV2.py](https://github.com/angel-one/smartapi-python/blob/main/SmartApi/smartWebSocketV2.py)
- [NSE circular on retail algo trading (Zerodha overview)](https://zerodha.com/z-connect/general/a-comprehensive-overview-of-nses-circular-on-the-new-retail-algo-trading-framework) · [NSE circular INVG67858](https://nsearchives.nseindia.com/content/circulars/INVG67858.pdf)
- [Amazon VPC pricing](https://aws.amazon.com/vpc/pricing/) · [NAT gateway pricing](https://docs.aws.amazon.com/vpc/latest/userguide/nat-gateway-pricing.html) · [Public IPv4 charge](https://aws.amazon.com/blogs/aws/new-aws-public-ipv4-address-charge-public-ip-insights)
- [MongoDB pricing](https://www.mongodb.com/pricing) · [Manage connections with AWS Lambda](https://www.mongodb.com/docs/atlas/manage-connections-aws-lambda/) · [Time series indexes](https://www.mongodb.com/docs/manual/core/timeseries/timeseries-index/) · [Data API EOL](https://www.mongodb.com/community/forums/t/mongodb-atlas-data-api-and-custom-https-endpoints-end-of-life-and-deprecation/296686)
