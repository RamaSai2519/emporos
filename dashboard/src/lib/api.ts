import { z } from "zod";
import {
  ApiSchema,
  type Command,
  type CommandInput,
  type Resource,
} from "./schema";
import type { paths } from "./api.generated";
import { WireSchema } from "./wire";
import { ReadModelMapper } from "./read-models";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}
export interface TokenProvider {
  get(): string | null;
}
export interface LoginGateway {
  login(passcode: string): Promise<z.infer<typeof ApiSchema.login>>;
}
export interface CommandGateway {
  submit(input: CommandInput): Promise<Command>;
  command(key: string, knownId?: string): Promise<Command>;
}
export interface ReadGateway {
  read<K extends Resource>(
    resource: K,
    signal?: AbortSignal,
  ): Promise<z.infer<(typeof ApiSchema.resources)[K]>>;
}

/** Owns authenticated HTTP transport, with no automatic mutation retries. */
export class ApiClient implements LoginGateway, CommandGateway, ReadGateway {
  constructor(
    readonly baseUrl: string,
    private readonly tokens: TokenProvider,
    private readonly request: typeof fetch,
  ) {
    const url = new URL(baseUrl);
    if (
      !["https:", "http:"].includes(url.protocol) ||
      (url.protocol === "http:" &&
        !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname))
    )
      throw new Error(
        "API URL must use HTTPS (HTTP is allowed only on loopback).",
      );
    if (url.username || url.password || url.search || url.hash)
      throw new Error(
        "API URL must not contain credentials, query parameters or fragments.",
      );
  }
  private readonly mapper = new ReadModelMapper(Date.now);
  private readonly loaders = {
    overview: async (signal?: AbortSignal) =>
      this.mapper.overview(
        await this.send("/overview", WireSchema.overview, { signal }),
      ),
    positions: async (signal?: AbortSignal) =>
      this.mapper.positions(
        await this.send(
          "/positions?open_only=true",
          z.array(WireSchema.position),
          { signal },
        ),
      ),
    orders: async (signal?: AbortSignal) =>
      this.mapper.orders(
        await this.send("/orders?limit=500", z.array(WireSchema.order), {
          signal,
        }),
      ),
    strategies: async (signal?: AbortSignal) =>
      this.mapper.strategies(
        await this.send("/strategies", z.array(WireSchema.strategy), {
          signal,
        }),
      ),
    risk: async (signal?: AbortSignal) =>
      this.mapper.risk(await this.send("/risk", WireSchema.risk, { signal })),
    market: async () => ({ as_of: new Date().toISOString(), items: [] }),
    system: async (signal?: AbortSignal) => {
      const [events, reconciliations, commands, health] = await Promise.all([
        this.send("/system/events", z.array(WireSchema.event), { signal }),
        this.send(
          "/system/reconciliations",
          z.array(WireSchema.reconciliation),
          { signal },
        ),
        this.send("/commands?limit=500", z.array(WireSchema.command), {
          signal,
        }),
        this.send("/health", WireSchema.health, { signal }),
      ]);
      const system = this.mapper.system(events, reconciliations, commands);
      return {
        ...system,
        services: [
          {
            name: "Control API",
            healthy: health.status === "ok",
            detail:
              "HTTP service health; worker and feed health are not exposed.",
          },
        ],
      };
    },
  };
  async login(passcode: string) {
    const response = await this.send(
      "/auth/login",
      WireSchema.token,
      { method: "POST", body: JSON.stringify({ passcode }) },
      false,
    );
    const expiresIn = (Date.parse(response.expires_at) - Date.now()) / 1000;
    if (expiresIn <= 0)
      throw new ApiError("The API returned an expired session.", 401);
    return {
      access_token: response.token,
      token_type: "bearer" as const,
      expires_in: expiresIn,
    };
  }
  async read<K extends Resource>(
    resource: K,
    signal?: AbortSignal,
  ): Promise<z.infer<(typeof ApiSchema.resources)[K]>> {
    const result = await this.loaders[resource](signal);
    return ApiSchema.resources[resource].parse(result) as z.infer<
      (typeof ApiSchema.resources)[K]
    >;
  }
  async submit(input: CommandInput) {
    const body: paths["/commands"]["post"]["requestBody"]["content"]["application/json"] =
      ApiSchema.commandInput.parse(input);
    return this.mapper.command(
      await this.send("/commands", WireSchema.command, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    );
  }
  async command(key: string, knownId?: string) {
    let id = knownId;
    if (!id) {
      const commands = await this.send(
        "/commands?limit=500",
        z.array(WireSchema.command),
      );
      id = commands.find((command) => command.idempotency_key === key)?.id;
    }
    if (!id)
      throw new ApiError(
        "Original command not found in the latest 500 records. Delivery remains unconfirmed.",
        404,
      );
    const detail = await this.send(
      `/commands/${encodeURIComponent(id)}`,
      WireSchema.commandDetail,
    );
    if (detail.command.idempotency_key !== key)
      throw new ApiError("Command identity mismatch.", 502);
    const result = this.mapper.command(detail.command);
    return {
      ...result,
      message: detail.command.reason || detail.results.at(-1)?.message || null,
    };
  }
  async orderHistory(id: string, signal?: AbortSignal) {
    return this.mapper.orderEvents(
      await this.send(
        `/orders/${encodeURIComponent(id)}`,
        WireSchema.orderDetail,
        { signal },
      ),
    );
  }
  async candles(
    instrument: string,
    interval: string,
    signal?: AbortSignal,
  ): Promise<z.infer<typeof ApiSchema.candles>> {
    signal?.throwIfAborted();
    throw new ApiError(
      `Candle data for ${instrument} (${interval}) is not exposed by this API.`,
      501,
    );
  }
  async stream(signal: AbortSignal, lastEventId: string): Promise<Response> {
    const response = await this.request(`${this.baseUrl}/stream`, {
      headers: {
        ...this.headers(),
        Accept: "text/event-stream",
        ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
      },
      signal,
      cache: "no-store",
      credentials: "omit",
    });
    if (!response.ok)
      throw new ApiError(
        `Live updates unavailable (${response.status}).`,
        response.status,
      );
    if (!response.headers.get("content-type")?.includes("text/event-stream"))
      throw new ApiError("Invalid live-update response.", 502);
    return response;
  }
  private headers(): Record<string, string> {
    const token = this.tokens.get();
    if (!token) throw new ApiError("Sign in to continue.", 401);
    return { Authorization: `Bearer ${token}` };
  }
  private async send<S extends z.ZodType>(
    path: string,
    schema: S,
    init: RequestInit = {},
    authenticated = true,
  ): Promise<z.output<S>> {
    const timeout = AbortSignal.timeout(15_000);
    const response = await this.request(`${this.baseUrl}${path}`, {
      ...init,
      signal: init.signal ? AbortSignal.any([init.signal, timeout]) : timeout,
      headers: {
        "Content-Type": "application/json",
        ...(authenticated ? this.headers() : {}),
      },
      cache: "no-store",
      credentials: "omit",
    });
    if (!response.ok) {
      const message =
        response.status === 401
          ? "Session expired or passcode incorrect. Sign in again."
          : response.status === 429
            ? "Too many requests. Wait before trying again."
            : `API request failed (${response.status}).`;
      throw new ApiError(message, response.status);
    }
    const parsed = schema.safeParse(await response.json());
    if (!parsed.success)
      throw new ApiError(
        "The API response does not match the dashboard contract. Data cannot be trusted.",
        502,
      );
    return parsed.data;
  }
}

/** Session-only token storage; expiry also handles an idle open dashboard. */
export class AuthSession implements TokenProvider {
  private token: string | null = null;
  private expiry = 0;
  constructor(
    private readonly storage: Storage,
    private readonly now: () => number,
  ) {}
  restore() {
    try {
      const stored = z
        .object({ token: z.string(), expiry: z.number() })
        .parse(JSON.parse(this.storage.getItem("emporos.session") ?? "null"));
      this.token = stored.token;
      this.expiry = stored.expiry;
    } catch {
      this.clear();
    }
    return this.get();
  }
  set(token: string, expiresIn: number) {
    this.token = token;
    this.expiry = this.now() + expiresIn * 1000;
    try {
      this.storage.setItem(
        "emporos.session",
        JSON.stringify({ token, expiry: this.expiry }),
      );
    } catch {
      /* In-memory session still works when storage is unavailable. */
    }
  }
  get() {
    if (this.now() >= this.expiry) {
      this.clear();
      return null;
    }
    return this.token;
  }
  clear() {
    this.token = null;
    this.expiry = 0;
    try {
      this.storage.removeItem("emporos.session");
    } catch {
      /* Already cleared in memory. */
    }
  }
}
