import { test, expect, type Locator, type Page } from "@playwright/test";

/**
 * The same acceptance flow paper.spec.ts proves, run against live_server.py instead: a real
 * LiveWorkerComposer session over the Angel One emulator (AngelOneBroker/FakeSmartApi), never a
 * real broker. Confirms the dashboard drives a LIVE-mode session exactly as it drives paper —
 * strategy control, manual orders through risk, cancel, the kill switch — with no code path here
 * that can reach a real order endpoint.
 */
class LiveAcceptance {
  private async confirm(
    page: Page,
    action: string,
    typed?: string,
    within?: Locator,
  ) {
    await (within ?? page)
      .getByRole("button", { name: action, exact: true })
      .first()
      .click();
    const dialog = page.getByRole("dialog");
    if (typed) await dialog.getByRole("textbox").fill(typed);
    await dialog
      .getByRole("button", {
        name: action === "Kill switch" ? "Activate kill switch" : action,
        exact: true,
      })
      .click();
  }
  private async outcome(page: Page, status: string) {
    await expect(
      page.getByRole("status").getByText(status, { exact: true }),
    ).toBeVisible({ timeout: 60_000 });
  }
  readonly workflow = async ({ page }: { page: Page }) => {
    test.setTimeout(300_000);
    await page.route("http://127.0.0.1:8000/**", (route) =>
      route.continue({ url: route.request().url().replace(":8000", ":8012") }),
    );
    await page.goto("/");
    await page
      .getByLabel("Passcode", { exact: true })
      .fill("dashboard-live-acceptance");
    await page.getByRole("button", { name: "Unlock workspace" }).click();
    await expect(
      page.getByRole("heading", { name: "Session overview" }),
    ).toBeVisible();
    await page.goto("/positions");
    await expect(page.getByRole("cell", { name: /NSE:3045/ })).toBeVisible({
      timeout: 120_000,
    });
    await page.goto("/strategies");
    const card = page.locator("section.panel").filter({
      hasText: "enter_once_test",
    });
    await expect(card.getByLabel("Backtest verdict: no verdict")).toBeVisible();
    await this.confirm(page, "Stop strategy", undefined, card);
    await this.outcome(page, "DONE");
    // Unlike paper, live has no typed-standing escape: a strategy with no recorded verdict is
    // refused even from the dashboard, with no acknowledgement able to get it started. Proving
    // that refusal here — not a restart — is the live-specific case paper.spec.ts cannot cover.
    await this.confirm(page, "Start strategy", "none", card);
    await this.outcome(page, "REJECTED");
    await page.goto("/orders");
    await page.getByLabel("Instrument", { exact: true }).fill("NSE:3045");
    await page.getByLabel("Quantity", { exact: true }).fill("10");
    await page.getByLabel("Limit price · ₹").fill("99");
    await page
      .getByLabel("Reason", { exact: true })
      .fill("Browser acceptance resting limit order (live/emulator)");
    await page.getByRole("button", { name: "Review order" }).click();
    await page.getByRole("dialog").getByRole("textbox").fill("PLACE ORDER");
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Place manual order", exact: true })
      .click();
    await this.outcome(page, "DONE");
    await expect(
      page
        .getByRole("button", { name: "Cancel", exact: true })
        .filter({ visible: true })
        .first(),
    ).toBeVisible();
    await page
      .locator("button:not([disabled])")
      .filter({ hasText: /^Cancel$/ })
      .first()
      .click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Cancel order", exact: true })
      .click();
    await this.outcome(page, "DONE");
    await page.getByLabel("Quantity", { exact: true }).fill("1000000");
    await page.getByLabel("Limit price · ₹").fill("100");
    await page.getByRole("button", { name: "Review order" }).click();
    await page.getByRole("dialog").getByRole("textbox").fill("PLACE ORDER");
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Place manual order", exact: true })
      .click();
    await this.outcome(page, "REJECTED");
    await this.confirm(page, "Kill switch", "HALT");
    await this.outcome(page, "DONE");
    await expect(
      page.getByRole("button", { name: "Kill switch active" }),
    ).toBeVisible({ timeout: 60_000 });
    await page.screenshot({
      path: "test-results/real-live-workflow.png",
      fullPage: true,
    });
  };
}
const suite = new LiveAcceptance();
test(
  "real live worker (emulator): browser controls reach risk and execution",
  suite.workflow,
);
