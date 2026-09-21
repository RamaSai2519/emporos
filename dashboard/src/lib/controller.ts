import { QueryClient, QueryObserver } from "@tanstack/react-query";
import { ApiClient, ApiError, AuthSession } from "./api";
import { CommandCoordinator, type CommandState } from "./commands";
import { LiveUpdates, type LiveStatus } from "./live";
import { ApiSchema, type Resource, type Snapshot } from "./schema";

export type DashboardState = Readonly<{
  authenticated: boolean;
  ready: boolean;
  snapshot: Snapshot;
  errors: Partial<Record<Resource, string>>;
  live: LiveStatus;
  now: number;
  command: CommandState | null;
  loginError: string | null;
  signingIn: boolean;
}>;
/** Composes transport, session, cache, live updates and command tracking for the view. */
export class DashboardController {
  /** Polls the visible view only while the SSE stream is down ("Polling · 2s"). */
  private static readonly FALLBACK_POLL_MS = 2_000;
  /** While live, a slow backstop for sources the SSE stream never reports (strategies, risk, mode). */
  private static readonly BACKSTOP_REFRESH_MS = 15_000;
  private state: DashboardState = {
    authenticated: false,
    ready: false,
    snapshot: {},
    errors: {},
    live: "connecting",
    now: Date.now(),
    command: null,
    loginError: null,
    signingIn: false,
  };
  private readonly cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 1500, refetchOnWindowFocus: true },
    },
  });
  private readonly subscriptions: (() => void)[] = [];
  private timer: ReturnType<typeof setInterval> | null = null;
  private backstop: ReturnType<typeof setInterval> | null = null;
  private readonly live: LiveUpdates;
  readonly commands: CommandCoordinator;
  constructor(
    readonly api: ApiClient,
    private readonly auth: AuthSession,
    storage: Storage,
    private readonly changed: (state: DashboardState) => void,
    /** The one resource the current page renders, plus the always-needed overview. */
    private readonly active: Resource,
  ) {
    this.commands = new CommandCoordinator(
      api,
      storage,
      () => crypto.randomUUID(),
      (command) => this.update({ command }),
    );
    this.live = new LiveUpdates(
      api,
      (resources) => {
        const targets =
          resources ?? (Object.keys(ApiSchema.resources) as Resource[]);
        void this.cache.invalidateQueries({
          predicate: (query) =>
            query.queryKey[0] === "read" &&
            targets.some((resource) => query.queryKey[1] === resource),
        });
      },
      (live) => this.update({ live }),
      () => this.logout("Your session expired. Sign in again."),
    );
  }
  start() {
    this.update({ ready: true });
    if (this.auth.restore()) this.connect();
  }
  async login(passcode: string) {
    if (this.state.signingIn) return;
    this.update({ signingIn: true, loginError: null });
    try {
      const result = await this.api.login(passcode);
      this.auth.set(result.access_token, result.expires_in);
      this.connect();
    } catch (error) {
      this.update({
        loginError:
          error instanceof Error ? error.message : "Cannot connect to the API.",
      });
    } finally {
      this.update({ signingIn: false });
    }
  }
  logout(message: string | null = null) {
    this.stop();
    this.auth.clear();
    this.cache.clear();
    this.update({
      authenticated: false,
      snapshot: {},
      errors: {},
      loginError: message,
      command: null,
    });
  }
  stop() {
    this.live.stop();
    for (const unsubscribe of this.subscriptions.splice(0)) unsubscribe();
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    if (this.backstop) clearInterval(this.backstop);
    this.backstop = null;
    void this.cache.cancelQueries();
  }
  private connect() {
    this.stop();
    this.update({ authenticated: true, loginError: null });
    this.commands.restore();
    for (const resource of new Set<Resource>(["overview", this.active]))
      this.observe(resource);
    this.live.start();
    this.timer = setInterval(() => {
      if (!this.auth.get()) {
        this.logout("Your session expired. Sign in again.");
        return;
      }
      this.update({ now: Date.now() });
      void this.commands.resolve();
      if (this.state.live !== "live") this.refreshVisible();
    }, DashboardController.FALLBACK_POLL_MS);
    this.backstop = setInterval(
      () => {
        if (this.state.live === "live") this.refreshVisible();
      },
      DashboardController.BACKSTOP_REFRESH_MS,
    );
  }
  /** Refetches only what the current page can show: its resource plus the overview. */
  private refreshVisible() {
    void this.cache.invalidateQueries({
      predicate: (query) =>
        query.queryKey[0] === "read" &&
        (query.queryKey[1] === this.active ||
          query.queryKey[1] === "overview"),
    });
  }
  private observe<K extends Resource>(resource: K) {
    const observer = new QueryObserver(this.cache, {
      queryKey: ["read", resource],
      queryFn: ({ signal }) => this.api.read(resource, signal),
    });
    this.subscriptions.push(
      observer.subscribe((result) => {
        if (result.error instanceof ApiError && result.error.status === 401) {
          this.logout("Your session expired. Sign in again.");
          return;
        }
        this.update({
          snapshot: result.data
            ? { ...this.state.snapshot, [resource]: result.data }
            : this.state.snapshot,
          errors: {
            ...this.state.errors,
            [resource]: result.error ? result.error.message : undefined,
          },
        });
      }),
    );
  }
  private update(patch: Partial<DashboardState>) {
    this.state = { ...this.state, ...patch };
    this.changed(this.state);
  }
}
