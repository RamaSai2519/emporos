"use client";
import { Component, type FormEvent } from "react";
import { Button } from "./ui/button";
import { Panel } from "./primitives";
import type { Action } from "./action-dialog";
import type { Snapshot } from "@/lib/schema";

export class OrderTicket extends Component<
  {
    market: Snapshot["market"];
    disabled: boolean;
    onAction: (action: Action) => void;
  },
  { error: string | null }
> {
  state: { error: string | null } = { error: null };
  private submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    const instrument = String(values.get("instrument") ?? "").trim();
    const quantity = Number(values.get("quantity"));
    const price = String(values.get("price"));
    const reason = String(values.get("reason") ?? "").trim();
    const side = values.get("side") === "SELL" ? "SELL" : "BUY";
    if (
      !/^((NSE)|(BSE)):[A-Za-z0-9_-]+$/.test(instrument) ||
      !Number.isSafeInteger(quantity) ||
      quantity < 1 ||
      !/^\d+(\.\d+)?$/.test(price) ||
      Number(price) <= 0 ||
      !reason
    ) {
      this.setState({
        error:
          "Choose an instrument and enter a positive whole quantity, limit price and reason.",
      });
      return;
    }
    this.setState({ error: null });
    this.props.onAction({
      title: "Place manual order",
      confirmation: "PLACE ORDER",
      description: `${side} ${quantity} ${instrument} at a limit of ₹${price}. Reason: ${reason}. This request must pass the same risk checks as a strategy order.`,
      danger: true,
      draft: {
        type: "PLACE_MANUAL_ORDER",
        params: {
          instrument_id: instrument,
          side,
          quantity,
          limit_price: price,
          reason,
        },
      },
    });
  };
  render() {
    return (
      <Panel title="Manual order" eyebrow="Risk checked">
        <form className="ticket" onSubmit={this.submit}>
          <label className="field" htmlFor="ticket-instrument">
            Instrument
          </label>
          <input
            id="ticket-instrument"
            name="instrument"
            required
            placeholder="NSE:3045"
            autoComplete="off"
          />
          <p className="small muted">
            Use the exact instrument ID from the order book or instrument
            master.
          </p>
          <div className="form-grid">
            <label className="field">
              Side
              <select name="side">
                <option>BUY</option>
                <option>SELL</option>
              </select>
            </label>
            <label className="field">
              Order type
              <input value="LIMIT" readOnly />
            </label>
            <label className="field">
              Quantity
              <input
                name="quantity"
                type="number"
                min="1"
                step="1"
                required
                placeholder="0"
              />
            </label>
            <label className="field">
              Limit price · ₹
              <input
                name="price"
                type="number"
                min="0.01"
                step="any"
                required
                placeholder="0.00"
              />
            </label>
          </div>
          <label className="field">
            Reason
            <input
              name="reason"
              required
              maxLength={500}
              placeholder="Why are you placing this order?"
            />
          </label>
          {this.state.error && (
            <p role="alert" className="negative small">
              {this.state.error}
            </p>
          )}
          <Button disabled={this.props.disabled} className="w-full">
            Review order
          </Button>
          <p className="small muted">
            Cash equity · Intraday · Limit orders only
          </p>
        </form>
      </Panel>
    );
  }
}
