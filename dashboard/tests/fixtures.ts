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
