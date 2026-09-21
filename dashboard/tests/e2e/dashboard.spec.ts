import { test, expect, type Page, type Route } from "@playwright/test";
import { ControlFixture } from "../fixtures";
import type { Command, CommandInput } from "../../src/lib/schema";

/** Browser interception is a UI contract test, not a replacement for paper-worker acceptance. */
class BrowserApiFixture {
  readonly data = new ControlFixture();
  readonly submissions: CommandInput[] = [];
  command: ReturnType<ControlFixture["command"]> | null = null;
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
          token: "browser-test-token",
          expires_at: new Date(Date.now() + 43200_000).toISOString(),
        },
      });
    expect(route.request().headers().authorization).toBe(
      "Bearer browser-test-token",
    );
    if (this.expire)
      return route.fulfill({ status: 401, json: { detail: "Expired" } });
    if (path === "/stream") return route.fulfill({ status: 503 });
    if (path === "/commands" && route.request().method() === "POST") {
      const input = route.request().postDataJSON() as CommandInput;
      this.submissions.push(input);
      this.command = this.data.command(input);
      if (this.ambiguous) return route.abort("connectionreset");
      return route.fulfill({ status: 202, json: this.command });
    }
    if (path.startsWith("/commands/"))
      return this.command
        ? route.fulfill({
            json: {
              command: this.command,
              results: [
                {
                  ts: new Date().toISOString(),
                  status: this.command.status,
                  message: this.command.reason,
                  data: {},
                },
              ],
            },
          })
        : route.fulfill({ status: 404 });
    if (path === "/commands")
      return route.fulfill({ json: this.command ? [this.command] : [] });
    if (path === "/orders/order-1")
      return route.fulfill({ json: this.data.orderDetail() });
    const responses: Record<string, unknown> = {
      "/overview": this.data.overview,
      "/positions": this.data.positions,
      "/orders": this.data.orders,
      "/strategies": this.data.strategies,
      "/risk": this.data.risk,
      "/system/events": this.data.events,
      "/system/reconciliations": [],
      "/health": { status: "ok", time: new Date().toISOString() },
    };
    if (path in responses) return route.fulfill({ json: responses[path] });
    throw new Error(
      `Dashboard called an endpoint absent from the API contract: ${path}`,
    );
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
    await expect(page.getByText("₹3,150.00", { exact: true })).toBeVisible();
  }
  finish(status: Command["status"]) {
    if (this.command)
      this.command = {
        ...this.command,
        status,
        reason:
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
      if (route === "market")
        await expect(
          page.getByText(
            "The current control API does not expose candle data.",
          ),
        ).toBeVisible();
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
    await page.getByLabel("Instrument", { exact: true }).fill("NSE:3045");
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
