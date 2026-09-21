import { test, expect, type Locator, type Page } from "@playwright/test";

/** Browser requests are forwarded unchanged to the real isolated API, never fulfilled by fixtures. */
class PaperAcceptance {
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
    test.setTimeout(240_000);
    await page.route("http://127.0.0.1:8000/**", (route) =>
      route.continue({ url: route.request().url().replace(":8000", ":8011") }),
    );
    await page.goto("/");
    await page
      .getByLabel("Passcode", { exact: true })
      .fill("dashboard-paper-acceptance");
    await page.getByRole("button", { name: "Unlock workspace" }).click();
    await expect(
      page.getByRole("heading", { name: "Session overview" }),
    ).toBeVisible();
    await page.goto("/positions");
    await expect(page.getByRole("cell", { name: /NSE:3045/ })).toBeVisible({
      timeout: 120_000,
    });
    await page.goto("/strategies");
    // The test strategy has never been through a curation: the page says so beside its controls.
    // (scoped to its card: the shared database may hold other strategies too)
    const card = page.locator("section.panel").filter({
      hasText: "enter_once_test",
    });
    await expect(card.getByLabel("Backtest verdict: no verdict")).toBeVisible();
    await this.confirm(page, "Stop strategy", undefined, card);
    await this.outcome(page, "DONE");
    // ... so starting it again needs its standing typed, and the worker checks it.
    await this.confirm(page, "Start strategy", "none", card);
    await this.outcome(page, "DONE");
    await page.goto("/orders");
    await page.getByLabel("Instrument", { exact: true }).fill("NSE:3045");
    await page.getByLabel("Quantity", { exact: true }).fill("10");
    await page.getByLabel("Limit price · ₹").fill("99");
    await page
      .getByLabel("Reason", { exact: true })
      .fill("Browser acceptance resting limit order");
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
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/real-paper-workflow.png",
      fullPage: true,
    });
  };
}
const suite = new PaperAcceptance();
test(
  "real paper worker: browser controls reach risk and execution",
  suite.workflow,
);
