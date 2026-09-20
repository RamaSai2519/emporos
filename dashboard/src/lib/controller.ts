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
  private readonly live: LiveUpdates;
  readonly commands: CommandCoordinator;
  constructor(
    readonly api: ApiClient,
    private readonly auth: AuthSession,
    storage: Storage,
    private readonly changed: (state: DashboardState) => void,
  ) {
    this.commands = new CommandCoordinator(
      api,
      storage,
      () => crypto.randomUUID(),
      (command) => this.update({ command }),
    );
    this.live = new LiveUpdates(
      api,
      (resource) => {
        void this.cache.invalidateQueries({
          queryKey: resource ? ["read", resource] : ["read"],
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
    void this.cache.cancelQueries();
  }
  private connect() {
    this.stop();
    this.update({ authenticated: true, loginError: null });
    this.commands.restore();
    for (const resource of Object.keys(ApiSchema.resources) as Resource[])
      this.observe(resource);
    this.live.start();
    this.timer = setInterval(() => {
      if (!this.auth.get()) {
        this.logout("Your session expired. Sign in again.");
        return;
      }
      this.update({ now: Date.now() });
      void this.commands.resolve();
    }, 2000);
  }
  private observe<K extends Resource>(resource: K) {
    const observer = new QueryObserver(this.cache, {
      queryKey: ["read", resource],
      queryFn: ({ signal }) => this.api.read(resource, signal),
      refetchInterval: 2000,
      refetchIntervalInBackground: true,
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
