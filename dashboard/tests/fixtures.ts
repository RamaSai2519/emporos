import type { Snapshot, Command, CommandInput } from "../src/lib/schema";

/** Test-only control-plane responses, never imported by the shipped application. */
export class PaperFixture {
  readonly snapshot: Required<Snapshot>;
  constructor(now = new Date().toISOString()) {
    this.snapshot = {
      overview: {
        as_of: now,
        session_state: "RUNNING",
        trading_mode: "paper",
        broker_healthy: true,
        feed_healthy: true,
        worker_healthy: true,
        stale_after_seconds: 30,
        kill_switch: false,
        day_pnl: "4280.50",
        realised_pnl: "3150.00",
        unrealised_pnl: "1130.50",
        open_positions: 2,
        open_orders: 1,
        equity_curve: [
          { at: new Date(Date.parse(now) - 60_000).toISOString(), value: 0 },
          { at: now, value: 4280.5 },
        ],
        events: [
          {
            id: "event-1",
            at: now,
            severity: "info",
            message: "Paper session started. Startup reconciliation passed.",
          },
        ],
      },
      positions: {
        as_of: now,
        items: [
          {
            id: "position-1",
            instrument_id: "nse-reliance",
            symbol: "RELIANCE",
            exchange: "NSE",
            strategy_id: "momentum_v1",
            quantity: 10,
            average_price: "1425.00",
            last_price: "1432.50",
            unrealised_pnl: "75.00",
            realised_pnl: "0",
            frozen: false,
          },
          {
            id: "position-2",
            instrument_id: "nse-tcs",
            symbol: "TCS",
            exchange: "NSE",
            strategy_id: "mean_reversion",
            quantity: 5,
            average_price: "3120.00",
            last_price: null,
            unrealised_pnl: null,
            realised_pnl: "0",
            frozen: false,
          },
        ],
      },
      orders: {
        as_of: now,
        items: [
          {
            id: "order-1",
            symbol: "RELIANCE",
            exchange: "NSE",
            side: "BUY",
            order_type: "LIMIT",
            quantity: 10,
            filled_quantity: 0,
            limit_price: "1425.00",
            state: "OPEN",
            created_at: now,
            events: [
              {
                sequence: 1,
                state: "PENDING_NEW",
                at: now,
                message: "Intent persisted",
              },
              {
                sequence: 2,
                state: "OPEN",
                at: now,
                message: "Order acknowledged",
              },
            ],
          },
        ],
      },
      strategies: {
        as_of: now,
        items: [
          {
            id: "momentum_v1",
            name: "Opening momentum",
            status: "stopped",
            enabled: true,
            standing: "validated",
            verdict: {
              outcome: "validated",
              recorded_at: "2026-09-21T00:00:00+05:30",
              capital: "50000",
              first_day: "2025-09-22",
              last_day: "2026-09-18",
              experiment: "fixture",
              source: "curation",
              gates: [],
              notes: [],
            },
            pnl: "4280.50",
            signals: 14,
            description:
              "Intraday momentum strategy for the configured cash-equity universe.",
            config: { lookback: 20, threshold: 0.5 },
          },
        ],
      },
      risk: {
        as_of: now,
        limits: [
          { name: "Daily loss budget", used: 1200, limit: 5000, unit: "INR" },
          { name: "Gross exposure", used: 52000, limit: 100000, unit: "INR" },
        ],
        events: [
          {
            id: "risk-1",
            at: now,
            severity: "warning",
            message:
              "Manual order rejected: instrument exposure limit exceeded.",
          },
        ],
      },
      market: {
        as_of: now,
        items: [
          {
            instrument_id: "nse-reliance",
            symbol: "RELIANCE",
            exchange: "NSE",
            last_price: "1432.50",
            change_percent: 0.53,
            as_of: now,
          },
          {
            instrument_id: "nse-tcs",
            symbol: "TCS",
            exchange: "NSE",
            last_price: "3120.00",
            change_percent: -0.25,
            as_of: now,
          },
        ],
      },
      system: {
        as_of: now,
        services: [
          { name: "Worker", healthy: true, detail: "Paper session running" },
          { name: "Market feed", healthy: true, detail: "Receiving updates" },
        ],
        reconciliations: [
          {
            id: "recon-1",
            at: now,
            severity: "info",
            message: "No reconciliation discrepancies.",
          },
        ],
        events: [],
        commands: [],
      },
    };
  }
  command(
    input: CommandInput,
    status: Command["status"] = "ACCEPTED",
  ): Command {
    return {
      id: "command-1",
      idempotency_key: input.idempotency_key,
      type: input.type,
      status,
      created_at: new Date().toISOString(),
      message:
        status === "REJECTED"
          ? "Rejected by the worker: exposure limit exceeded."
          : null,
    };
  }
}
export class MemoryStorage implements Storage {
  private values = new Map<string, string>();
  get length() {
    return this.values.size;
  }
  clear() {
    this.values.clear();
  }
  getItem(key: string) {
    return this.values.get(key) ?? null;
  }
  key(index: number) {
    return [...this.values.keys()][index] ?? null;
  }
  removeItem(key: string) {
    this.values.delete(key);
  }
  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
}

/** Responses matching the generated FastAPI contract, including collection/detail envelopes. */
export class ControlFixture {
  private readonly now = new Date().toISOString();
  readonly overview: import("../src/lib/api.generated").components["schemas"]["OverviewDto"] =
    {
      session_state: "RUNNING",
      session_state_at: this.now,
      kill_switch: { halted: false, reason: "" },
      reconciliation: null,
      open_positions: 1,
      realised_pnl: "3150.00",
      unrealised_pnl: "75.00",
      fees: "20.00",
      trades: 1,
      snapshot_at: this.now,
      pending_commands: 0,
    };
  readonly positions: import("../src/lib/api.generated").components["schemas"]["PositionDto"][] =
    [
      {
        instrument_id: "NSE:3045",
        net_quantity: 10,
        average_price: "100.00",
        realised_pnl: "0",
        fees: null,
        updated_at: this.now,
      },
    ];
  readonly orders: import("../src/lib/api.generated").components["schemas"]["OrderDto"][] =
    [
      {
        id: "order-1",
        ordertag: "fixture-order",
        instrument_id: "NSE:3045",
        side: "BUY",
        order_type: "LIMIT",
        quantity: 10,
        filled_quantity: 0,
        limit_price: "99.00",
        average_price: null,
        state: "OPEN",
        strategy_run_id: "run-1",
        signal_id: "signal-1",
        parent_order_id: null,
        reprice_count: 0,
        status_message: "",
        created_at: this.now,
        updated_at: this.now,
      },
    ];
  readonly strategies: import("../src/lib/api.generated").components["schemas"]["StrategyDto"][] =
    [
      {
        name: "momentum_v1",
        config_hash: "test-config",
        last_run_id: "run-1",
        last_run_date: "2026-09-18",
        signals_last_run: 14,
        enabled: true,
        status: "stopped",
        standing: "validated",
        verdict: {
          outcome: "validated",
          recorded_at: "2026-09-21T00:00:00+05:30",
          capital: "50000",
          first_day: "2025-09-22",
          last_day: "2026-09-18",
          experiment: "fixture",
          source: "curation",
          gates: [],
          notes: [],
        },
      },
    ];
  readonly risk = {
    limits: { max_position_value: "50000", max_daily_loss: "5000" },
    recent_rejections: [],
  };
  readonly events = [
    {
      id: "event-1",
      type: "session_state",
      ts: this.now,
      data: { to: "RUNNING" },
    },
  ];
  orderDetail() {
    return {
      order: this.orders[0]!,
      events: [
        {
          seq: 1,
          ts: this.now,
          state: "PENDING_NEW",
          filled_quantity: 0,
          reason: "Intent persisted",
        },
        {
          seq: 2,
          ts: this.now,
          state: "OPEN",
          filled_quantity: 0,
          reason: "Order acknowledged",
        },
      ],
    };
  }
  command(
    input: CommandInput,
    status: Command["status"] = "ACCEPTED",
  ): import("../src/lib/api.generated").components["schemas"]["CommandDto"] {
    return {
      id: "command-1",
      idempotency_key: input.idempotency_key,
      type: input.type,
      status,
      params: input.params,
      issued_by: "operator",
      created_at: this.now,
      updated_at: this.now,
      expires_at: null,
      reason: "",
      attempts: 0,
    };
  }
}
