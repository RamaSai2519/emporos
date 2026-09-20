'use client';
import { Component, type FormEvent } from 'react';
import { Button } from './ui/button';
import { Panel } from './primitives';
import type { Action } from './action-dialog';
import type { Snapshot } from '@/lib/schema';

export class OrderTicket extends Component<{ market: Snapshot['market']; disabled: boolean; onAction: (action: Action) => void }, { error: string | null; type: 'LIMIT' | 'STOPLOSS_LIMIT' }> {
  state: { error: string | null; type: 'LIMIT' | 'STOPLOSS_LIMIT' } = { error: null, type: 'LIMIT' };
  private submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const values = new FormData(event.currentTarget);
    const instrument = this.props.market?.items.find(item => item.instrument_id === values.get('instrument'));
    const quantity = Number(values.get('quantity')); const price = String(values.get('price')); const trigger = String(values.get('trigger') ?? '');
    const side = values.get('side') === 'SELL' ? 'SELL' : 'BUY';
    if (!instrument || !Number.isSafeInteger(quantity) || quantity < 1 || !/^\d+(\.\d+)?$/.test(price) || Number(price) <= 0 || (this.state.type === 'STOPLOSS_LIMIT' && (!/^\d+(\.\d+)?$/.test(trigger) || Number(trigger) <= 0 || (side === 'BUY' ? Number(trigger) > Number(price) : Number(trigger) < Number(price))))) { this.setState({ error: 'Enter a valid quantity and price. Stop-limit buys require limit ≥ trigger; sells require limit ≤ trigger.' }); return; }
    this.setState({ error: null }); this.props.onAction({ title: 'Place manual order', confirmation: 'PLACE ORDER', description: `${side} ${quantity} ${instrument.symbol} at a limit of ₹${price}. This request must pass the same risk checks as a strategy order.`, danger: true, draft: { type: 'PLACE_MANUAL_ORDER', params: { instrument_id: instrument.instrument_id, side, quantity, order_type: this.state.type, limit_price: price, ...(this.state.type === 'STOPLOSS_LIMIT' ? { trigger_price: trigger } : {}) } } });
  };
  render() { return <Panel title="Manual order" eyebrow="Risk checked"><form className="ticket" onSubmit={this.submit}><label className="field">Instrument<select name="instrument" required><option value="">Select from watchlist</option>{this.props.market?.items.map(item => <option key={item.instrument_id} value={item.instrument_id}>{item.symbol} · {item.exchange}</option>)}</select></label><div className="form-grid"><label className="field">Side<select name="side"><option>BUY</option><option>SELL</option></select></label><label className="field">Order type<select value={this.state.type} onChange={event => this.setState({ type: event.target.value as 'LIMIT' | 'STOPLOSS_LIMIT' })}><option>LIMIT</option><option>STOPLOSS_LIMIT</option></select></label><label className="field">Quantity<input name="quantity" type="number" min="1" step="1" required placeholder="0" /></label><label className="field">Limit price · ₹<input name="price" type="number" min="0.01" step="any" required placeholder="0.00" /></label></div>{this.state.type === 'STOPLOSS_LIMIT' && <label className="field">Trigger price · ₹<input name="trigger" type="number" min="0.01" step="any" required /></label>}{this.state.error && <p role="alert" className="negative small">{this.state.error}</p>}<Button disabled={this.props.disabled || !this.props.market?.items.length} className="w-full">Review order</Button><p className="small muted">Cash equity · Intraday · Limit orders only</p></form></Panel>; }
}
