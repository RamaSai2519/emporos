import { z } from "zod";
import { ApiSchema } from "./schema";

/** Runtime validation of the control API's persisted read models. */
export class WireSchema {
  static readonly token = z.object({
    token: z.string().min(1),
    expires_at: ApiSchema.time,
  });
  static readonly reconciliation = z.object({
    id: z.string(),
    ts: ApiSchema.time,
    status: z.string(),
    trigger: z.string().nullable().optional(),
    discrepancies: z.array(z.record(z.string(), z.string())).default([]),
    healed: z.array(z.record(z.string(), z.string())).default([]),
  });
  static readonly overview = z.object({
    session_state: z.string().nullable(),
    session_state_at: ApiSchema.time.nullable(),
    kill_switch: z
      .object({ halted: z.boolean(), reason: z.string().default("") })
      .nullable(),
    reconciliation: this.reconciliation.nullable(),
    open_positions: z.number().int(),
    cash: ApiSchema.money.nullable(),
    realised_pnl: ApiSchema.money.nullable(),
    unrealised_pnl: ApiSchema.money.nullable(),
    fees: ApiSchema.money.nullable(),
    trades: z.number().int().nullable(),
    snapshot_at: ApiSchema.time.nullable(),
    pending_commands: z.number().int(),
    trading_mode: z.enum(["paper", "live"]).nullable(),
    broker_healthy: z.boolean().nullable(),
    feed_healthy: z.boolean().nullable(),
    worker_healthy: z.boolean().nullable(),
  });
  static readonly position = z.object({
    instrument_id: z.string(),
    net_quantity: z.number().int(),
    average_price: ApiSchema.money,
    realised_pnl: ApiSchema.money,
    fees: ApiSchema.money.nullable(),
    updated_at: ApiSchema.time,
  });
  static readonly order = z.object({
    id: z.string(),
    ordertag: z.string(),
    instrument_id: z.string(),
    side: z.enum(["BUY", "SELL"]),
    order_type: z.enum(["LIMIT", "STOPLOSS_LIMIT"]),
    quantity: z.number().int(),
    filled_quantity: z.number().int(),
    limit_price: ApiSchema.money,
    average_price: ApiSchema.money.nullable(),
    state: z.string(),
    strategy_run_id: z.string().nullable(),
    signal_id: z.string().nullable(),
    parent_order_id: z.string().nullable(),
    reprice_count: z.number().int(),
    status_message: z.string(),
    created_at: ApiSchema.time,
    updated_at: ApiSchema.time,
  });
  static readonly orderDetail = z.object({
    order: this.order,
    events: z.array(
      z.object({
        seq: z.number().int(),
        ts: ApiSchema.time,
        state: z.string(),
        filled_quantity: z.number().int().nullable(),
        reason: z.string(),
      }),
    ),
  });
  static readonly strategy = z.object({
    name: z.string(),
    config_hash: z.string().nullable(),
    last_run_id: z.string().nullable(),
    last_run_date: z.string().nullable(),
    signals_last_run: z.number().int(),
    enabled: z.boolean(),
    status: z.enum(["running", "stopped"]),
    standing: ApiSchema.standing,
    verdict: z
      .object({
        outcome: z.enum(["validated", "inconclusive", "rejected"]),
        recorded_at: ApiSchema.time,
        capital: z.string(),
        first_day: z.string(),
        last_day: z.string(),
        experiment: z.string(),
        source: z.string(),
        gates: z.array(
          z.object({
            name: z.string(),
            outcome: z.enum(["pass", "fail", "unknown"]),
            detail: z.string(),
          }),
        ),
        notes: z.array(z.string()),
      })
      .nullable(),
  });
  static readonly risk = z.object({
    limits: z.record(z.string(), z.string()),
    recent_rejections: z.array(
      z.object({
        id: z.string(),
        ts: ApiSchema.time,
        rule: z.string(),
        reason: z.string(),
        instrument_id: z.string().nullable(),
        strategy_run_id: z.string().nullable(),
        signal_id: z.string().nullable(),
      }),
    ),
  });
  static readonly event = z.object({
    id: z.string(),
    type: z.string(),
    ts: ApiSchema.time,
    data: z.record(z.string(), z.unknown()),
  });
  static readonly command = z.object({
    id: z.string(),
    idempotency_key: z.string(),
    type: z.string(),
    status: ApiSchema.command.shape.status,
    params: z.record(z.string(), z.unknown()),
    issued_by: z.string(),
    created_at: ApiSchema.time,
    updated_at: ApiSchema.time.nullable(),
    expires_at: ApiSchema.time.nullable(),
    reason: z.string(),
    attempts: z.number().int(),
  });
  static readonly commandDetail = z.object({
    command: this.command,
    results: z.array(
      z.object({
        ts: ApiSchema.time,
        status: z.string().nullable(),
        message: z.string(),
        data: z.record(z.string(), z.unknown()),
      }),
    ),
  });
  static readonly health = z.object({
    status: z.string(),
    time: ApiSchema.time,
  });
}

/** Compile-time coupling to every used backend response DTO, in addition to runtime validation. */
export class WireContract {
  static readonly responses = {
    TokenResponse: WireSchema.token,
    OverviewDto: WireSchema.overview,
    PositionDto: WireSchema.position,
    OrderDto: WireSchema.order,
    OrderDetailDto: WireSchema.orderDetail,
    StrategyDto: WireSchema.strategy,
    RiskDto: WireSchema.risk,
    SystemEventDto: WireSchema.event,
    CommandDto: WireSchema.command,
    CommandDetailDto: WireSchema.commandDetail,
    HealthDto: WireSchema.health,
  } satisfies {
    [
      K in
        | "TokenResponse"
        | "OverviewDto"
        | "PositionDto"
        | "OrderDto"
        | "OrderDetailDto"
        | "StrategyDto"
        | "RiskDto"
        | "SystemEventDto"
        | "CommandDto"
        | "CommandDetailDto"
        | "HealthDto"
    ]: z.ZodType<import("./api.generated").components["schemas"][K]>;
  };
}
