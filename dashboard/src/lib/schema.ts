import { z } from "zod";

/** Read models are supplied by the worker; the browser never derives P&L. */
export class ApiSchema {
  static readonly money = z.string().regex(/^-?\d+(\.\d+)?$/);
  static readonly time = z.iso.datetime({ offset: true });
  static readonly event = z.object({
    id: z.string(),
    at: this.time,
    severity: z.enum(["info", "warning", "error"]),
    message: z.string(),
  });
  static readonly command = z.object({
    id: z.string(),
    idempotency_key: z.string(),
    type: z.string(),
    status: z.enum([
      "PENDING",
      "ACCEPTED",
      "EXECUTING",
      "DONE",
      "FAILED",
      "REJECTED",
      "EXPIRED",
    ]),
    created_at: this.time,
    message: z.string().nullable(),
  });
  static readonly overview = z.object({
    as_of: this.time,
    session_state: z.string(),
    trading_mode: z.enum(["paper", "live"]),
    broker_healthy: z.boolean(),
    feed_healthy: z.boolean(),
    worker_healthy: z.boolean(),
    stale_after_seconds: z.number().positive(),
    kill_switch: z.boolean(),
    day_pnl: this.money.nullable(),
    realised_pnl: this.money.nullable(),
    unrealised_pnl: this.money.nullable(),
    open_positions: z.number().int().nonnegative(),
    open_orders: z.number().int().nonnegative(),
    equity_curve: z.array(
      z.object({ at: this.time, value: z.number().finite() }),
    ),
    events: z.array(this.event),
  });
  static readonly position = z.object({
    id: z.string(),
    instrument_id: z.string(),
    symbol: z.string(),
    exchange: z.enum(["NSE", "BSE"]),
    strategy_id: z.string(),
    quantity: z.number().int(),
    average_price: this.money,
    last_price: this.money.nullable(),
    unrealised_pnl: this.money.nullable(),
    realised_pnl: this.money,
    frozen: z.boolean(),
  });
  static readonly positions = z.object({
    as_of: this.time,
    items: z.array(this.position),
  });
  static readonly order = z.object({
    id: z.string(),
    symbol: z.string(),
    exchange: z.enum(["NSE", "BSE"]),
    side: z.enum(["BUY", "SELL"]),
    order_type: z.enum(["LIMIT", "STOPLOSS_LIMIT"]),
    quantity: z.number().int(),
    filled_quantity: z.number().int(),
    limit_price: this.money,
    state: z.string(),
    created_at: this.time,
    events: z.array(
      z.object({
        sequence: z.number().int(),
        state: z.string(),
        at: this.time,
        message: z.string().nullable(),
      }),
    ),
  });
  static readonly orders = z.object({
    as_of: this.time,
    items: z.array(this.order),
  });
  static readonly strategy = z.object({
    id: z.string(),
    name: z.string(),
    status: z.enum(["running", "stopped", "halted", "starting", "stopping"]),
    pnl: this.money.nullable(),
    signals: z.number().int(),
    description: z.string(),
    config: z.record(z.string(), z.unknown()),
  });
  static readonly strategies = z.object({
    as_of: this.time,
    items: z.array(this.strategy),
  });
  static readonly risk = z.object({
    as_of: this.time,
    limits: z.array(
      z.object({
        name: z.string(),
        used: z.number().nonnegative(),
        limit: z.number().positive(),
        unit: z.string(),
      }),
    ),
    events: z.array(this.event),
  });
  static readonly quote = z.object({
    instrument_id: z.string(),
    symbol: z.string(),
    exchange: z.enum(["NSE", "BSE"]),
    last_price: this.money.nullable(),
    change_percent: z.number().nullable(),
    as_of: this.time,
  });
  static readonly market = z.object({
    as_of: this.time,
    items: z.array(this.quote),
  });
  static readonly candles = z.object({
    items: z.array(
      z.object({
        time: z.number().int(),
        open: z.number(),
        high: z.number(),
        low: z.number(),
        close: z.number(),
      }),
    ),
  });
  static readonly system = z.object({
    as_of: this.time,
    services: z.array(
      z.object({ name: z.string(), healthy: z.boolean(), detail: z.string() }),
    ),
    reconciliations: z.array(this.event),
    events: z.array(this.event),
    commands: z.array(this.command),
  });
  static readonly login = z.object({
    access_token: z.string().min(1),
    token_type: z.literal("bearer"),
    expires_in: z.number().positive(),
  });
  static readonly commandInput = z.discriminatedUnion("type", [
    z.object({
      type: z.literal("SET_KILL_SWITCH"),
      params: z.strictObject({ halted: z.literal(true), reason: z.string().min(1) }),
      idempotency_key: z.string().uuid(),
    }),
    z.object({
      type: z.literal("SQUARE_OFF_ALL"),
      params: z.strictObject({}),
      idempotency_key: z.string().uuid(),
    }),
    z.object({
      type: z.literal("CLOSE_POSITION"),
      params: z.strictObject({ instrument_id: z.string().min(1) }),
      idempotency_key: z.string().uuid(),
    }),
    z.object({
      type: z.literal("CANCEL_ORDER"),
      params: z.strictObject({ order_id: z.string().min(1) }),
      idempotency_key: z.string().uuid(),
    }),
    z.object({
      type: z.literal("START_STRATEGY"),
      params: z.strictObject({ name: z.string().min(1) }),
      idempotency_key: z.string().uuid(),
    }),
    z.object({
      type: z.literal("STOP_STRATEGY"),
      params: z.strictObject({ name: z.string().min(1) }),
      idempotency_key: z.string().uuid(),
    }),
    z.object({
      type: z.literal("UPDATE_STRATEGY_CONFIG"),
      params: z.strictObject({
        name: z.string().min(1),
        config: z.record(z.string(), z.unknown()),
        force: z.literal(false),
      }),
      idempotency_key: z.string().uuid(),
    }),
    z.object({
      type: z.literal("PLACE_MANUAL_ORDER"),
      params: z.strictObject({
        instrument_id: z.string().min(1),
        side: z.enum(["BUY", "SELL"]),
        quantity: z.number().int().positive(),
        limit_price: this.money.refine((value) => Number(value) > 0),
        reason: z.string().min(1),
      }),
      idempotency_key: z.string().uuid(),
    }),
    z.object({
      type: z.literal("RECONCILE_NOW"),
      params: z.strictObject({}),
      idempotency_key: z.string().uuid(),
    }),
  ]);
  static readonly resources = {
    overview: this.overview,
    positions: this.positions,
    orders: this.orders,
    strategies: this.strategies,
    risk: this.risk,
    market: this.market,
    system: this.system,
  };
}
export type Resource = keyof typeof ApiSchema.resources;
export type Overview = z.infer<typeof ApiSchema.overview>;
export type Position = z.infer<typeof ApiSchema.position>;
export type Order = z.infer<typeof ApiSchema.order>;
export type Strategy = z.infer<typeof ApiSchema.strategy>;
export type Command = z.infer<typeof ApiSchema.command>;
export type CommandInput = z.infer<typeof ApiSchema.commandInput>;
export type CommandDraft = CommandInput extends infer C
  ? C extends CommandInput
    ? Omit<C, "idempotency_key">
    : never
  : never;
export type Event = z.infer<typeof ApiSchema.event>;
export type Snapshot = {
  [K in Resource]?: z.infer<(typeof ApiSchema.resources)[K]>;
};
