import { test, expect, type Page, type Route } from "@playwright/test";
import { PaperFixture } from "../fixtures";
import type { Command, CommandInput } from "../../src/lib/schema";

/** Browser interception is a UI contract test, not a replacement for paper-worker acceptance. */
class BrowserApiFixture {
  readonly data = new PaperFixture();
  readonly submissions: CommandInput[] = [];
  command: Command | null = null;
  expire = false;
  ambiguous = false;
  async install(page: Page) {
    await page.route("http://127.0.0.1:8000/**", this.route);
  }
  private route = async (route: Route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/auth/login")
      return route.fulfill({
        json: {
          access_token: "browser-test-token",
          token_type: "bearer",
          expires_in: 43200,
        },
      });
    expect(route.request().headers().authorization).toBe(
      "Bearer browser-test-token",
    );
    if (this.expire)
      return route.fulfill({ status: 401, json: { detail: "Expired" } });
    if (path === "/api/events") return route.fulfill({ status: 503 });
    if (path === "/api/commands" && route.request().method() === "POST") {
      const input = route.request().postDataJSON() as CommandInput;
      this.submissions.push(input);
      this.command = this.data.command(input);
      if (this.ambiguous) return route.abort("connectionreset");
      return route.fulfill({ status: 202, json: this.command });
    }
    if (path.startsWith("/api/commands/"))
      return this.command
        ? route.fulfill({ json: this.command })
        : route.fulfill({ status: 404 });
    if (path === "/api/candles")
      return route.fulfill({
        json: {
          items: [
            {
              time: 1758253500,
              open: 1420,
              high: 1435,
              low: 1418,
              close: 1432,
            },
            {
              time: 1758253800,
              open: 1432,
              high: 1437,
              low: 1425,
              close: 1428,
            },
          ],
        },
      });
    const key = path.slice(5) as keyof typeof this.data.snapshot;
    const snapshot = this.data.snapshot[key];
    if (snapshot)
      return route.fulfill({
        json: { ...snapshot, as_of: new Date().toISOString() },
      });
    return route.fulfill({ status: 404 });
  };
  async login(page: Page) {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "Welcome to your desk." }),
    ).toBeVisible();
    await page.getByLabel("Passcode", { exact: true }).fill("test-only");
    await page.getByRole("button", { name: "Unlock workspace" }).click();
    await expect(
      page.getByRole("heading", { name: "Session overview" }),
    ).toBeVisible();
    await expect(page.getByText("₹4,280.50", { exact: true })).toBeVisible();
  }
  finish(status: Command["status"]) {
    if (this.command)
      this.command = {
        ...this.command,
        status,
        message:
          status === "REJECTED"
            ? "Risk rejected: instrument exposure limit exceeded."
            : "Worker reported completion.",
      };
  }
}
class BrowserTests {
  readonly routes = async ({ page }: { page: Page }) => {
    const api = new BrowserApiFixture();
    await api.install(page);
    await api.login(page);
    await page.screenshot({
      path: "test-results/overview-desktop.png",
      fullPage: true,
    });
    for (const [route, heading] of [
      ["positions", "Positions"],
      ["orders", "Orders"],
      ["strategies", "Strategies"],
      ["risk", "Risk monitor"],
      ["market", "Market watch"],
      ["backtests", "Backtests"],
      ["system", "System"],
    ]) {
      await page.goto(`/${route}`);
      await expect(
        page.getByRole("heading", { name: heading, exact: true }).first(),
      ).toBeVisible();
      if (route === "market") {
        await page.getByRole("button", { name: /RELIANCE.*NSE/ }).click();
        await expect(
          page.getByRole("img", { name: "RELIANCE candlestick chart" }),
        ).toBeVisible();
      }
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "Session overview" }),
    ).toBeVisible();
    await page.screenshot({
      path: "test-results/overview-mobile.png",
      fullPage: true,
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(page.getByRole("navigation")).toBeVisible();
  };
  readonly killSwitch = async ({ page }: { page: Page }) => {
    const api = new BrowserApiFixture();
    await api.install(page);
    await api.login(page);
    await page
      .getByRole("button", { name: "Kill switch", exact: true })
      .click();
    const dialog = page.getByRole("dialog");
    const confirm = dialog.getByRole("button", {
      name: "Activate kill switch",
      exact: true,
    });
    await expect(confirm).toBeDisabled();
    await dialog.getByRole("textbox").fill("HALT");
    await confirm.dblclick();
    await expect(page.getByRole("status")).toContainText("ACCEPTED");
    expect(api.submissions).toHaveLength(1);
    await expect(page.getByRole("status")).not.toContainText("DONE");
    api.finish("DONE");
    await expect(page.getByRole("status")).toContainText("DONE");
  };
  readonly strategy = async ({ page }: { page: Page }) => {
    const api = new BrowserApiFixture();
    await api.install(page);
    await api.login(page);
    await page.goto("/strategies");
    await page
      .getByRole("button", { name: "Start strategy", exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Start strategy", exact: true })
      .click();
    await expect(page.getByRole("status")).toContainText("ACCEPTED");
    api.data.snapshot.strategies.items[0]!.status = "running";
    api.finish("DONE");
    await expect(
      page.getByRole("button", { name: "Stop strategy", exact: true }),
    ).toBeEnabled();
    await page
      .getByRole("button", { name: "Stop strategy", exact: true })
      .click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Stop strategy", exact: true })
      .click();
    await expect.poll(() => api.submissions.length).toBe(2);
    expect(api.submissions.map((item) => item.type)).toEqual([
      "START_STRATEGY",
      "STOP_STRATEGY",
    ]);
  };
  readonly order = async ({ page }: { page: Page }) => {
    const api = new BrowserApiFixture();
    await api.install(page);
    await api.login(page);
    await page.goto("/orders");
    await page.getByRole("button", { name: "History", exact: true }).click();
    await expect(page.getByText("Intent persisted")).toBeVisible();
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Cancel order", exact: true })
      .click();
    await expect(page.getByRole("status")).toContainText("ACCEPTED");
    api.finish("DONE");
    await expect(page.getByRole("status")).toContainText("DONE");
    await page
      .getByLabel("Instrument", { exact: true })
      .selectOption("nse-reliance");
    await page.getByLabel("Quantity", { exact: true }).fill("10");
    await page.getByLabel("Limit price · ₹").fill("1425");
    await page
      .getByLabel("Reason", { exact: true })
      .fill("Paper workflow verification");
    await page.getByRole("button", { name: "Review order" }).click();
    await page.getByRole("dialog").getByRole("textbox").fill("PLACE ORDER");
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Place manual order", exact: true })
      .click();
    await expect.poll(() => api.submissions.length).toBe(2);
    api.finish("REJECTED");
    await expect(page.getByRole("status")).toContainText(
      "Risk rejected: instrument exposure limit exceeded.",
    );
  };
  readonly restart = async ({ page }: { page: Page }) => {
    const api = new BrowserApiFixture();
    api.ambiguous = true;
    await api.install(page);
    await api.login(page);
    await page.goto("/orders");
    await page.getByRole("button", { name: "Cancel", exact: true }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Cancel order", exact: true })
      .click();
    await expect(page.getByRole("status")).toContainText("unconfirmed");
    await page.reload();
    await expect(page.getByRole("status")).toContainText("ACCEPTED");
    expect(api.submissions).toHaveLength(1);
    api.finish("DONE");
    await expect(page.getByRole("status")).toContainText("DONE");
  };
  readonly expired = async ({ page }: { page: Page }) => {
    const api = new BrowserApiFixture();
    await api.install(page);
    await api.login(page);
    api.expire = true;
    await expect(
      page.getByRole("heading", { name: "Welcome to your desk." }),
    ).toBeVisible({ timeout: 10_000 });
    await expect(
      page.getByRole("alert").filter({ hasText: "session expired" }),
    ).toContainText("session expired");
  };
}
const suite = new BrowserTests();
test(
  "all routes and mobile layout render with authenticated API data",
  suite.routes,
);
test(
  "kill switch requires confirmation and only reports worker outcomes",
  suite.killSwitch,
);
test("strategy start and stop use the command bus", suite.strategy);
test("order history, cancellation and manual-order rejection", suite.order);
test(
  "lost command response resolves after reload without duplicate submission",
  suite.restart,
);
test("expired credentials clear the workspace", suite.expired);
