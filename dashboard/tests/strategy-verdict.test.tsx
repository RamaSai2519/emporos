import { test, expect, afterEach } from "vitest";
import { render, cleanup, fireEvent, screen } from "@testing-library/react";
import { ApiSchema, type Strategy } from "../src/lib/schema";
import { StrategyCard } from "../src/components/views";
import type { Action } from "../src/components/action-dialog";

const failed = {
  name: "profit after costs is real",
  outcome: "fail" as const,
  detail: "net -3136.27, 95% interval -3719.24 to -2528.62",
};
const unknown = {
  name: "beats the luck of the search",
  outcome: "unknown" as const,
  detail: "too few trials",
};
const passed = {
  name: "drawdown within the budget",
  outcome: "pass" as const,
  detail: "3.1% of 10%",
};

function verdict(outcome: "validated" | "inconclusive" | "rejected") {
  return {
    outcome,
    recorded_at: "2026-09-21T00:00:00+05:30",
    capital: "50000",
    first_day: "2025-09-22",
    last_day: "2026-09-18",
    experiment: "em118",
    source: "curation",
    gates: [failed, unknown, passed],
    notes: [
      "Judged with each position sized to ₹5000; this config sizes at ₹25000.",
    ],
  };
}
function strategy(overrides: Partial<Strategy> = {}): Strategy {
  return ApiSchema.strategy.parse({
    id: "orb_v1",
    name: "orb_v1",
    status: "stopped",
    enabled: false,
    standing: "rejected",
    verdict: verdict("rejected"),
    pnl: null,
    signals: 0,
    description: "",
    config: null,
    ...overrides,
  });
}
function card(item: Strategy) {
  const actions: Action[] = [];
  render(
    <StrategyCard
      strategy={item}
      disabled={false}
      onAction={(action) => actions.push(action)}
    />,
  );
  return actions;
}
/** The one action the card raised (the test fails if it raised none or several). */
function only(actions: Action[]): Action {
  expect(actions).toHaveLength(1);
  return actions[0] as Action;
}
const pressed = (name: string) =>
  fireEvent.click(screen.getByRole("button", { name }));

class VerdictTests {
  readonly rejectedShowsItsReasons = () => {
    card(strategy());
    const panel = screen.getByLabelText("Backtest verdict: rejected");
    expect(panel.textContent).toContain("rejected");
    expect(panel.textContent).toContain("profit after costs is real");
    expect(panel.textContent).toContain("Judged at ₹50000");
    expect(panel.textContent).toContain("1 gate not resolved");
    expect(panel.textContent).not.toContain("drawdown within the budget");
    expect(panel.textContent).toContain("Judged with each position sized to");
  };
  readonly noVerdictSaysSo = () => {
    card(strategy({ standing: "none", verdict: null }));
    expect(
      screen.getByLabelText("Backtest verdict: no verdict").textContent,
    ).toContain("has not been through a curation");
  };
  readonly staleIsNotPresentedAsEvidence = () => {
    card(strategy({ standing: "stale", verdict: verdict("validated") }));
    const text = screen.getByLabelText("Backtest verdict: stale").textContent;
    expect(text).toContain("for an earlier configuration");
    expect(text).toContain("curate it again");
  };
  readonly startingARejectedStrategyNeedsItsStandingTyped = () => {
    const actions = card(strategy());
    pressed("Start strategy");
    expect(actions).toHaveLength(1);
    expect(only(actions).confirmation).toBe("rejected");
    expect(only(actions).draft).toEqual({
      type: "START_STRATEGY",
      params: { name: "orb_v1", acknowledge: "rejected" },
    });
    expect(only(actions).description).toContain("paper only");
  };
  readonly everyStandingButValidatedNeedsAcknowledgement = () => {
    for (const standing of ["inconclusive", "stale", "none"] as const) {
      const actions = card(strategy({ standing }));
      pressed("Start strategy");
      expect(only(actions).confirmation).toBe(standing);
      cleanup();
    }
  };
  readonly aValidatedStrategyStartsWithoutCeremony = () => {
    const actions = card(
      strategy({ standing: "validated", verdict: verdict("validated") }),
    );
    pressed("Start strategy");
    expect(only(actions).confirmation).toBeUndefined();
    expect(only(actions).draft).toEqual({
      type: "START_STRATEGY",
      params: { name: "orb_v1" },
    });
  };
  readonly aRunningStrategyStopsWithoutCeremony = () => {
    const actions = card(strategy({ status: "running" }));
    pressed("Stop strategy");
    expect(only(actions).confirmation).toBeUndefined();
    expect(only(actions).draft).toEqual({
      type: "STOP_STRATEGY",
      params: { name: "orb_v1" },
    });
  };
  readonly theStartCommandCarriesOnlyAWellFormedAcknowledgement = () => {
    const start = (params: object) =>
      ApiSchema.commandInput.safeParse({
        type: "START_STRATEGY",
        params,
        idempotency_key: crypto.randomUUID(),
      }).success;
    expect(start({ name: "orb_v1" })).toBe(true);
    expect(start({ name: "orb_v1", acknowledge: "rejected" })).toBe(true);
    expect(start({ name: "orb_v1", acknowledge: "" })).toBe(false);
    expect(start({ name: "orb_v1", force: true })).toBe(false);
  };
}
const tests = new VerdictTests();
afterEach(cleanup);
test("a rejected verdict shows why", tests.rejectedShowsItsReasons);
test("a strategy that was never curated says so", tests.noVerdictSaysSo);
test(
  "a verdict for an edited config is stale, not evidence",
  tests.staleIsNotPresentedAsEvidence,
);
test(
  "starting a rejected strategy needs its standing typed",
  tests.startingARejectedStrategyNeedsItsStandingTyped,
);
test(
  "inconclusive, stale and unrated all need acknowledging",
  tests.everyStandingButValidatedNeedsAcknowledgement,
);
test(
  "a validated strategy starts without ceremony",
  tests.aValidatedStrategyStartsWithoutCeremony,
);
test(
  "a running strategy stops without ceremony",
  tests.aRunningStrategyStopsWithoutCeremony,
);
test(
  "the start command takes only a well-formed acknowledgement",
  tests.theStartCommandCarriesOnlyAWellFormedAcknowledgement,
);
