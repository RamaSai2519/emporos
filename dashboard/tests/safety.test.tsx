import { test, expect } from "vitest";
import { render, cleanup } from "@testing-library/react";
import {
  ApiClient,
  ApiError,
  AuthSession,
  type CommandGateway,
} from "../src/lib/api";
import { CommandCoordinator } from "../src/lib/commands";
import { SseDecoder } from "../src/lib/live";
import type { CommandInput, Command } from "../src/lib/schema";
import { ApiSchema } from "../src/lib/schema";
import { PositionsTable } from "../src/components/views";
import { ActionDialog } from "../src/components/action-dialog";
import { MemoryStorage, PaperFixture } from "./fixtures";

class GatewayFake implements CommandGateway {
  readonly calls: CommandInput[] = [];
  result: Command | null = null;
  ambiguous = false;
  async submit(input: CommandInput) {
    this.calls.push(input);
    if (this.ambiguous) throw new Error("Connection reset");
    return new PaperFixture().command(input);
  }
  async command(key: string) {
    if (!this.result) throw new ApiError("Not found", 404);
    expect(this.result.idempotency_key).toBe(key);
    return this.result;
  }
}
class SafetyTests {
  readonly doubleSubmit = async () => {
    const gateway = new GatewayFake();
    const coordinator = new CommandCoordinator(
      gateway,
      new MemoryStorage(),
      () => crypto.randomUUID(),
      () => {},
    );
    await Promise.all([
      coordinator.submit({
        type: "CANCEL_ORDER",
        params: { order_id: "order-1" },
      }),
      coordinator.submit({
        type: "CANCEL_ORDER",
        params: { order_id: "order-1" },
      }),
    ]);
    expect(gateway.calls).toHaveLength(1);
    expect(coordinator.state.result?.status).toBe("ACCEPTED");
    expect(coordinator.pending).toBe(true);
  };
  readonly restart = async () => {
    const storage = new MemoryStorage();
    const gateway = new GatewayFake();
    gateway.ambiguous = true;
    const first = new CommandCoordinator(
      gateway,
      storage,
      () => crypto.randomUUID(),
      () => {},
    );
    await first.submit({ type: "SQUARE_OFF_ALL", params: {} });
    expect(first.state.unresolved).toBe(true);
    const second = new CommandCoordinator(
      gateway,
      storage,
      () => crypto.randomUUID(),
      () => {},
    );
    second.restore();
    await second.submit({ type: "SQUARE_OFF_ALL", params: {} });
    await second.resolve();
    expect(gateway.calls).toHaveLength(1);
    expect(second.pending).toBe(true);
    gateway.result = new PaperFixture().command(gateway.calls[0]!, "REJECTED");
    await second.resolve();
    expect(second.state.result?.status).toBe("REJECTED");
    expect(second.pending).toBe(false);
    expect(storage.getItem("emporos.command")).toBeNull();
  };
  readonly tokenExpiry = () => {
    let now = 1000;
    const storage = new MemoryStorage();
    const session = new AuthSession(storage, () => now);
    session.set("test-session", 1);
    expect(session.get()).toBe("test-session");
    now = 2001;
    expect(session.get()).toBeNull();
    expect(storage.length).toBe(0);
  };
  readonly sse = () => {
    const events: [string, string | undefined][] = [];
    const parser = new SseDecoder((data, id) => events.push([data, id]));
    parser.push(': heartbeat\r\n\r\nid: 12\r\ndata: {"resource":');
    parser.push('"orders"}\r\n\r');
    parser.push("\ndata: one\ndata: two\n\n");
    expect(events).toEqual([
      ['{"resource":"orders"}', "12"],
      ["one\ntwo", undefined],
    ]);
  };
  readonly missingMarks = () => {
    const api = new ApiClient(
      "http://127.0.0.1:8000",
      { get: () => "test-token" },
      fetch,
    );
    const view = render(
      <PositionsTable
        snapshot={new PaperFixture().snapshot}
        api={api}
        disabled={false}
        onAction={() => {}}
      />,
    );
    expect(view.getAllByText("Unavailable")).toHaveLength(2);
    cleanup();
  };
  readonly confirmation = () => {
    const view = render(
      <ActionDialog
        action={{
          title: "Square off all",
          description: "Close all positions",
          confirmation: "SQUARE OFF ALL",
          draft: { type: "SQUARE_OFF_ALL", params: {} },
        }}
        onClose={() => {}}
        onSubmit={() => {}}
        disabled={false}
      />,
    );
    expect(
      view
        .getByRole("button", { name: "Square off all" })
        .hasAttribute("disabled"),
    ).toBe(true);
    cleanup();
  };
  readonly forbiddenOrders = () => {
    expect(
      ApiSchema.commandInput.safeParse({
        type: "PLACE_MANUAL_ORDER",
        idempotency_key: crypto.randomUUID(),
        params: {
          instrument_id: "test",
          side: "BUY",
          order_type: "MARKET",
          quantity: 1,
          limit_price: "1",
        },
      }).success,
    ).toBe(false);
  };
  readonly transport = async () => {
    const gateway = new HttpFake();
    const api = new ApiClient(
      "http://127.0.0.1:8000",
      { get: () => "session-token" },
      gateway.fetch,
    );
    await api.read("overview");
    expect(gateway.authorization).toBe("Bearer session-token");
    expect(gateway.url).not.toContain("session-token");
    gateway.invalid = true;
    await expect(api.read("overview")).rejects.toThrow("does not match");
  };
}
class HttpFake {
  authorization: string | null = null;
  url = "";
  invalid = false;
  readonly fetch: typeof fetch = async (input, init) => {
    this.url = String(input);
    this.authorization = new Headers(init?.headers).get("Authorization");
    return Response.json(
      this.invalid ? {} : new PaperFixture().snapshot.overview,
    );
  };
}
const suite = new SafetyTests();
test(
  "double-submit emits exactly one command and accepted is not done",
  suite.doubleSubmit,
);
test(
  "ambiguous delivery survives reload and resolves without resubmitting",
  suite.restart,
);
test("expired sessions are cleared from session storage", suite.tokenExpiry);
test(
  "SSE supports chunk boundaries, CRLF, heartbeats and multiline frames",
  suite.sse,
);
test(
  "missing marks render unavailable instead of invented P&L",
  suite.missingMarks,
);
test("destructive action requires typed confirmation", suite.confirmation);
test(
  "forbidden order types cannot pass the command schema",
  suite.forbiddenOrders,
);
test(
  "authenticated transport validates responses and keeps token out of URL",
  suite.transport,
);
