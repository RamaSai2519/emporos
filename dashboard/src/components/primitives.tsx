"use client";
import { Component, type ReactNode } from "react";
import { Activity, Inbox } from "lucide-react";
import { Display } from "@/lib/format";
import type { Event } from "@/lib/schema";

export class Badge extends Component<{ children: ReactNode; tone?: string }> {
  render() {
    return (
      <span className={`badge ${this.props.tone ?? ""}`}>
        {this.props.children}
      </span>
    );
  }
}
export class Panel extends Component<{
  title: string;
  eyebrow?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}> {
  render() {
    return (
      <section className={`panel ${this.props.className ?? ""}`}>
        <div className="panel-heading">
          <div>
            {this.props.eyebrow && (
              <span className="eyebrow">{this.props.eyebrow}</span>
            )}
            <h2>{this.props.title}</h2>
          </div>
          {this.props.action}
        </div>
        {this.props.children}
      </section>
    );
  }
}
export class Empty extends Component<{ title: string; children?: ReactNode }> {
  render() {
    return (
      <div className="empty">
        <Inbox size={27} strokeWidth={1.4} />
        <h3>{this.props.title}</h3>
        <p>{this.props.children}</p>
      </div>
    );
  }
}
export class DataTable extends Component<{
  headings: string[];
  rows: { id: string; cells: ReactNode[] }[];
  empty?: string;
}> {
  render() {
    return this.props.rows.length ? (
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {this.props.headings.map((heading) => (
                <th key={heading}>{heading}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {this.props.rows.map((row) => (
              <tr key={row.id}>
                {row.cells.map((cell, index) => (
                  <td key={index}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ) : (
      <Empty title={this.props.empty ?? "Nothing to show yet"}>
        Records will appear here when the worker publishes them.
      </Empty>
    );
  }
}
export class EventFeed extends Component<{ events: Event[]; empty?: string }> {
  render() {
    return this.props.events.length ? (
      <ul className="event-list">
        {this.props.events.map((event) => (
          <li key={event.id}>
            <Activity
              size={15}
              className={
                event.severity === "error"
                  ? "negative"
                  : event.severity === "warning"
                    ? "warning"
                    : "muted"
              }
            />
            <div>
              <p>{event.message}</p>
              <time dateTime={event.at}>{Display.time(event.at)} IST</time>
            </div>
          </li>
        ))}
      </ul>
    ) : (
      <Empty title={this.props.empty ?? "No events recorded"}>
        New events will appear automatically.
      </Empty>
    );
  }
}
