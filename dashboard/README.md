# Emporos dashboard — EM-41

Next.js App Router dashboard for a single operator. All code in this directory is frontend-owned; no worker, API, broker or persistence implementation is included.

## Run locally

```bash
cd dashboard
npm ci
cp .env.example .env.local
npm run dev
```

Open http://127.0.0.1:3000. The control API defaults to http://127.0.0.1:8000. It must allow the dashboard origin, Authorization and Content-Type headers, and Last-Event-ID for reconnects. Production API URLs must use HTTPS. Login uses `POST /auth/login` and a single passcode. JWTs stay in session storage and are removed on expiry/logout; no passcode is stored. No sample data is shipped in the application.

## Integration status — read before using

**The EM-40 API is not implemented in the current repository.** `contracts/openapi.json` is an explicit frontend integration proposal, not a schema exported from a running FastAPI server. Runtime response validation fails closed on incompatible data. The dashboard can be developed and tested independently, but paper-worker acceptance remains blocked on the API. Do not mark EM-41 Done until that integration and acceptance pass.

Command payloads follow the EM-40 control models: halt uses `halted`/`reason`, close uses `instrument_id`, strategies use `name`, and manual limit orders carry an audit `reason`. Read models and HTTP response envelopes still need reconciliation.

The API owner should reconcile the proposal with the real OpenAPI schema. Use `npm run contract:generate` after agreed schema changes. Generated types in `src/lib/api.generated.ts` are consumed by the command transport. `npm run contract:check` is part of the build and fails on stale generated artifacts. Set `EMPOROS_OPENAPI_URL` and run `npm run contract:verify` to detect differences against the real backend; this intentionally fails until the proposal is reconciled. API authentication, authorization, login throttling, CORS and worker-side command/risk enforcement are backend responsibilities and are not claimed as verified by frontend tests.

| Endpoint                                       | Frontend expectation                                                            |
| ---------------------------------------------- | ------------------------------------------------------------------------------- |
| `POST /auth/login`                             | `{passcode}` → `{access_token, token_type: "bearer", expires_in}`               |
| `GET /api/overview`                            | Worker snapshot, session state/mode, health, freshness, P&L, equity series      |
| `GET /api/positions`                           | `{as_of, items}` with marks and worker-computed P&L; missing marks are null     |
| `GET /api/orders`                              | Order book including sequenced state events                                     |
| `GET /api/strategies`                          | Status, P&L, signal count and configuration per strategy                        |
| `GET /api/risk`                                | Risk limit utilisation and rejection events                                     |
| `GET /api/market`                              | Watchlist with quotes, instrument IDs and quote timestamps                      |
| `GET /api/candles?instrument_id=…&interval=5m` | Ordered unique OHLC bars, Unix UTC seconds                                      |
| `GET /api/system`                              | Health, reconciliation/system events and command audit                          |
| `POST /api/commands`                           | `{type, params, idempotency_key}` → HTTP 202 with durable command record (uppercase status)        |
| `GET /api/commands/{idempotency_key}`          | Original command and authoritative status; 404 does not imply safe resubmission |
| `GET /api/events`                              | Authenticated SSE; `data: {"resource":"orders"}`; heartbeat at most 15s apart   |

All API reads and mutations use `Authorization: Bearer …`. SSE uses fetch streaming directly to the API, preserving auth headers without query-string tokens or a long-lived Vercel proxy. Cache invalidation follows events; a two-second refresh also guards against lost events. Interrupted or stalled streams reconnect and show polling status. All timestamps display in Asia/Kolkata.

## Controls and recovery

- Eight routes: overview, positions, orders, strategies, risk, market and system are implemented; backtests is the placeholder requested by EM-41.
- Destructive actions require typed confirmation. Strategy start/stop, order cancellation, configuration review and reconciliation also use confirmation dialogs.
- The manual-order endpoint accepts LIMIT orders only; the order book also represents STOPLOSS_LIMIT orders created by the worker. The browser performs input checks; the worker remains authoritative for prices, tick/circuit bounds and all risk checks.
- Paper/live is a read-only mode indicator. No runtime mode switch exists.
- P&L is displayed from API values. Missing marks show “Unavailable”.
- Command intent and UUID are saved **before** HTTP. Duplicate clicks are suppressed. Accepted/executing never appear as successful completion. The original UUID is retained after transport failure and resolved by lookup after reload. There is no automatic POST retry.
- An unconfirmed command locks further submissions. A 404 lookup leaves it unconfirmed; the operator must inspect the backend audit before clearing corrupted/unresolvable browser state. Do not clear it simply to retry a financial action.
- Stale/unavailable worker state pauses order and strategy controls. Kill-switch submission remains available during stale data unless another command is unresolved. If the API or command path is unavailable, use the independent emergency CLI/SSM path.
- Session storage is required for commands. Storage failure prevents sending an intent that cannot survive reload.

## Verification

```bash
npm run typecheck
npm run lint
npm test
npx playwright install chromium
npm run test:e2e
npm run build
npm run check:boundaries
npm audit
```

Playwright intercepts the control API using test-only fixtures; it validates UI flows, bearer headers, durable status rendering, duplicate-submit protection, expiry, responsive layout and reload recovery. It **does not** run a real paper worker or prove backend auth/risk behavior. The fixtures are outside `src` and never imported by the application. Tests write desktop/mobile screenshots under `test-results/`.

Before closing EM-41, run the same workflow against the real local paper worker: start/stop strategy, observe a fill update, inspect order history, cancel an order, submit a risk-rejected manual order, activate the kill switch and verify its persisted outcome. Verify missing/expired/tampered JWT rejection at the API and generated schema compatibility. Production deployment and Caddy hardening belong to the later production epic.

## Structure

- `lib/schema.ts`: validated immutable read and command DTO types.
- `lib/api.ts`: transport interfaces, validated HTTP client, session lifecycle.
- `lib/commands.ts`: restart-safe command coordinator.
- `lib/live.ts`: bounded SSE parser and reconnect lifecycle.
- `lib/controller.ts`: composition root and TanStack Query cache observers.
- `components/`: class-based React views, composed controls and shadcn-style Radix primitives; no domain trading behavior.
- `scripts/`: class-based contract generation/verification and boundary checks.

The slate-blue design uses a persistent session strip, restrained mint/rose financial states, tabular data and responsive navigation. Charts use Lightweight Charts with TradingView attribution.
