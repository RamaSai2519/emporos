"use client";
import { Component } from "react";
import { Badge } from "./primitives";
import type { Strategy } from "@/lib/schema";

type Standing = Strategy["standing"];
type Gate = NonNullable<Strategy["verdict"]>["gates"][number];

/** How each standing reads, and what it takes to start a strategy that holds it. */
export class Standings {
  static label(standing: Standing): string {
    return standing === "none" ? "no verdict" : standing;
  }
  static tone(standing: Standing): string {
    if (standing === "validated") return "positive";
    if (standing === "rejected") return "negative";
    return "warning";
  }
  /** Only a validated strategy starts without the operator naming its standing. */
  static needsAcknowledgement(standing: Standing): boolean {
    return standing !== "validated";
  }
}

export class VerdictPanel extends Component<{ strategy: Strategy }> {
  private summary() {
    const { standing, verdict } = this.props.strategy;
    if (standing === "none" || !verdict)
      return "No verdict is recorded: this strategy has not been through a curation.";
    if (standing === "stale")
      return `A ${verdict.outcome} verdict is recorded, but for an earlier configuration of this strategy. It says nothing about the current one: curate it again.`;
    if (standing === "validated")
      return "Every gate passed on data the strategy had not been tuned on.";
    if (standing === "rejected")
      return "At least one gate showed the strategy losing money or breaking a limit.";
    return "No gate failed, but the evidence is too thin to call it validated.";
  }
  private gates(wanted: Gate["outcome"]): Gate[] {
    return (this.props.strategy.verdict?.gates ?? []).filter(
      (gate) => gate.outcome === wanted,
    );
  }
  render() {
    const { standing, verdict } = this.props.strategy;
    const failing = this.gates("fail");
    const unresolved = this.gates("unknown");
    return (
      <div
        className="verdict"
        data-standing={standing}
        aria-label={`Backtest verdict: ${Standings.label(standing)}`}
      >
        <div className="verdict-head">
          <span className="eyebrow">Backtest verdict</span>
          <Badge tone={Standings.tone(standing)}>
            {Standings.label(standing)}
          </Badge>
        </div>
        <p className="small">{this.summary()}</p>
        {verdict && (
          <p className="small muted">
            Judged at ₹{verdict.capital} on {verdict.first_day} to{" "}
            {verdict.last_day} · {verdict.experiment} · recorded{" "}
            {verdict.recorded_at.slice(0, 10)} ({verdict.source})
          </p>
        )}
        {verdict?.notes.map((note) => (
          <p key={note} className="small muted verdict-note">
            {note}
          </p>
        ))}
        {failing.length > 0 && (
          <ul className="verdict-gates negative" aria-label="Failed gates">
            {failing.map((gate) => (
              <li key={gate.name}>
                <strong>{gate.name}</strong> — {gate.detail}
              </li>
            ))}
          </ul>
        )}
        {unresolved.length > 0 && (
          <details>
            <summary>
              {unresolved.length} gate{unresolved.length === 1 ? "" : "s"} not
              resolved
            </summary>
            <ul className="verdict-gates muted">
              {unresolved.map((gate) => (
                <li key={gate.name}>
                  <strong>{gate.name}</strong> — {gate.detail}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    );
  }
}
