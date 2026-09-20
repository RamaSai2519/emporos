import { z } from 'zod';
import { WireSchema } from './wire';
import { ApiSchema, type Command, type Event, type Snapshot, type Order } from './schema';

/** Maps persisted API facts into display models; absent worker metrics stay explicitly unknown. */
export class ReadModelMapper {
  constructor(private readonly now: () => number) {}
  overview(raw: z.infer<typeof WireSchema.overview>): NonNullable<Snapshot['overview']> {
    return { as_of: raw.snapshot_at ?? raw.session_state_at ?? new Date(0).toISOString(), session_state: raw.session_state ?? 'UNKNOWN', trading_mode: 'unknown', broker_healthy: null, feed_healthy: null, worker_healthy: null, stale_after_seconds: 30, kill_switch: raw.kill_switch?.halted ?? false, day_pnl: null, realised_pnl: raw.realised_pnl, unrealised_pnl: raw.unrealised_pnl, open_positions: raw.open_positions, open_orders: null, equity_curve: [], events: raw.reconciliation ? [this.reconciliation(raw.reconciliation)] : [] };
  }
  positions(raw: z.infer<typeof WireSchema.position>[]): NonNullable<Snapshot['positions']> {
    return { as_of: this.received(), items: raw.map(item => ({ id: item.instrument_id, instrument_id: item.instrument_id, symbol: item.instrument_id, exchange: 'UNKNOWN', strategy_id: 'Attribution unavailable', quantity: item.net_quantity, average_price: item.average_price, last_price: null, unrealised_pnl: null, realised_pnl: item.realised_pnl, frozen: false })) };
  }
  orders(raw: z.infer<typeof WireSchema.order>[]): NonNullable<Snapshot['orders']> {
    return { as_of: this.received(), items: raw.map(item => ({ id: item.id, symbol: item.instrument_id, exchange: 'UNKNOWN', side: item.side, order_type: item.order_type, quantity: item.quantity, filled_quantity: item.filled_quantity, limit_price: item.limit_price, state: item.state, created_at: item.created_at, events: [] })) };
  }
  orderEvents(raw: z.infer<typeof WireSchema.orderDetail>): Order['events'] { return raw.events.map(event => ({ sequence: event.seq, at: event.ts, state: event.state, message: event.reason })); }
  strategies(raw: z.infer<typeof WireSchema.strategy>[]): NonNullable<Snapshot['strategies']> {
    return { as_of: this.received(), items: raw.map(item => ({ id: item.name, name: item.name, status: 'unknown', pnl: null, signals: item.signals_last_run, description: item.last_run_date ? `Last recorded run: ${item.last_run_date}. Live status and configuration are not published by this API.` : 'No recorded run. Live status and configuration are not published by this API.', config: null })) };
  }
  risk(raw: z.infer<typeof WireSchema.risk>): NonNullable<Snapshot['risk']> { return { as_of: this.received(), limits: [], configured_limits: raw.limits, events: raw.recent_rejections.map(item => ({ id: item.id, at: item.ts, severity: 'warning', message: `${item.rule}: ${item.reason}` })) }; }
  command(raw: z.infer<typeof WireSchema.command>): Command { return ApiSchema.command.parse({ ...raw, message: raw.reason || null }); }
  system(events: z.infer<typeof WireSchema.event>[], reconciliations: z.infer<typeof WireSchema.reconciliation>[], commands: z.infer<typeof WireSchema.command>[]): NonNullable<Snapshot['system']> {
    return { as_of: this.received(), services: [], events: events.map(item => ({ id: item.id, at: item.ts, severity: 'info', message: `${item.type}: ${JSON.stringify(item.data)}` })), reconciliations: reconciliations.map(item => this.reconciliation(item)), commands: commands.map(item => this.command(item)) };
  }
  private reconciliation(item: z.infer<typeof WireSchema.reconciliation>): Event { return { id: item.id, at: item.ts, severity: item.discrepancies.length ? 'error' : 'info', message: `Reconciliation ${item.status}${item.trigger ? ` · ${item.trigger}` : ''}${item.discrepancies.length ? `: ${JSON.stringify(item.discrepancies)}` : ''}` }; }
  private received() { return new Date(this.now()).toISOString(); }
}
