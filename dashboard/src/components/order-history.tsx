"use client";
import { Component } from "react";
import type { ApiClient } from "@/lib/api";
import type { Order } from "@/lib/schema";
import { Display } from "@/lib/format";
import { Empty } from "./primitives";

export class OrderHistory extends Component<
  { id: string; api: ApiClient },
  { events: Order["events"] | null; error: string | null }
> {
  state = {
    events: null as Order["events"] | null,
    error: null as string | null,
  };
  private abort: AbortController | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  componentDidMount() {
    void this.refresh();
    this.timer = setInterval(() => void this.refresh(), 2000);
  }
  componentWillUnmount() {
    this.abort?.abort();
    if (this.timer) clearInterval(this.timer);
  }
  private async refresh() {
    if (this.abort) return;
    const abort = new AbortController();
    this.abort = abort;
    try {
      const events = await this.props.api.orderHistory(
        this.props.id,
        abort.signal,
      );
      if (!abort.signal.aborted) this.setState({ events, error: null });
    } catch (error) {
      if (!abort.signal.aborted)
        this.setState({
          error:
            error instanceof Error
              ? error.message
              : "Order history unavailable.",
        });
    } finally {
      this.abort = null;
    }
  }
  render() {
    return (
      <>
        {this.state.error && (
          <p role="alert" className="error-banner">
            {this.state.error}
          </p>
        )}
        {this.state.events?.length ? (
          <ol className="timeline">
            {this.state.events.map((event) => (
              <li key={event.sequence}>
                <span className="sequence">{event.sequence}</span>
                <div>
                  <strong>{event.state}</strong>
                  <p>{event.message}</p>
                  <time>{Display.time(event.at)} IST</time>
                </div>
              </li>
            ))}
          </ol>
        ) : (
          <Empty
            title={
              this.state.events
                ? "No recorded transitions"
                : "Loading order history…"
            }
          />
        )}
      </>
    );
  }
}
