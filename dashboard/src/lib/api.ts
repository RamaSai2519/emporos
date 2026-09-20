import { z } from 'zod';
import { ApiSchema, type Command, type CommandInput, type Resource } from './schema';
import type { paths } from './api.generated';

export class ApiError extends Error {
  constructor(message: string, readonly status: number) { super(message); }
}
export interface TokenProvider { get(): string | null; }
export interface LoginGateway { login(passcode: string): Promise<z.infer<typeof ApiSchema.login>>; }
export interface CommandGateway { submit(input: CommandInput): Promise<Command>; command(key: string): Promise<Command>; }
export interface ReadGateway { read<K extends Resource>(resource: K, signal?: AbortSignal): Promise<z.infer<(typeof ApiSchema.resources)[K]>>; }

/** Owns authenticated HTTP transport, with no automatic mutation retries. */
export class ApiClient implements LoginGateway, CommandGateway, ReadGateway {
  constructor(readonly baseUrl: string, private readonly tokens: TokenProvider, private readonly request: typeof fetch) {
    const url = new URL(baseUrl);
    if (!['https:', 'http:'].includes(url.protocol) || (url.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(url.hostname))) throw new Error('API URL must use HTTPS (HTTP is allowed only on loopback).');
    if (url.username || url.password || url.search || url.hash) throw new Error('API URL must not contain credentials, query parameters or fragments.');
  }
  async login(passcode: string) {
    return this.send('/auth/login', ApiSchema.login, { method: 'POST', body: JSON.stringify({ passcode }) }, false);
  }
  async read<K extends Resource>(resource: K, signal?: AbortSignal): Promise<z.infer<(typeof ApiSchema.resources)[K]>> {
    return this.send(`/api/${resource}`, ApiSchema.resources[resource], { signal });
  }
  async submit(input: CommandInput) {
    const body: paths['/api/commands']['post']['requestBody']['content']['application/json'] = ApiSchema.commandInput.parse(input);
    return this.send('/api/commands', ApiSchema.command, { method: 'POST', body: JSON.stringify(body) });
  }
  async command(key: string) { return this.send(`/api/commands/${encodeURIComponent(key)}`, ApiSchema.command); }
  async candles(instrument: string, interval: string, signal?: AbortSignal) {
    return this.send(`/api/candles?${new URLSearchParams({ instrument_id: instrument, interval })}`, ApiSchema.candles, { signal });
  }
  async stream(signal: AbortSignal, lastEventId: string): Promise<Response> {
    const response = await this.request(`${this.baseUrl}/api/events`, { headers: { ...this.headers(), Accept: 'text/event-stream', ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}) }, signal, cache: 'no-store', credentials: 'omit' });
    if (!response.ok) throw new ApiError(`Live updates unavailable (${response.status}).`, response.status);
    if (!response.headers.get('content-type')?.includes('text/event-stream')) throw new ApiError('Invalid live-update response.', 502);
    return response;
  }
  private headers(): Record<string, string> {
    const token = this.tokens.get();
    if (!token) throw new ApiError('Sign in to continue.', 401);
    return { Authorization: `Bearer ${token}` };
  }
  private async send<S extends z.ZodType>(path: string, schema: S, init: RequestInit = {}, authenticated = true): Promise<z.output<S>> {
    const timeout = AbortSignal.timeout(15_000);
    const response = await this.request(`${this.baseUrl}${path}`, { ...init, signal: init.signal ? AbortSignal.any([init.signal, timeout]) : timeout, headers: { 'Content-Type': 'application/json', ...(authenticated ? this.headers() : {}) }, cache: 'no-store', credentials: 'omit' });
    if (!response.ok) {
      const message = response.status === 401 ? 'Session expired or passcode incorrect. Sign in again.' : response.status === 429 ? 'Too many requests. Wait before trying again.' : `API request failed (${response.status}).`;
      throw new ApiError(message, response.status);
    }
    const parsed = schema.safeParse(await response.json());
    if (!parsed.success) throw new ApiError('The API response does not match the dashboard contract. Data cannot be trusted.', 502);
    return parsed.data;
  }
}

/** Session-only token storage; expiry also handles an idle open dashboard. */
export class AuthSession implements TokenProvider {
  private token: string | null = null;
  private expiry = 0;
  constructor(private readonly storage: Storage, private readonly now: () => number) {}
  restore() {
    try {
      const stored = z.object({ token: z.string(), expiry: z.number() }).parse(JSON.parse(this.storage.getItem('emporos.session') ?? 'null'));
      this.token = stored.token; this.expiry = stored.expiry;
    } catch { this.clear(); }
    return this.get();
  }
  set(token: string, expiresIn: number) {
    this.token = token; this.expiry = this.now() + expiresIn * 1000;
    try { this.storage.setItem('emporos.session', JSON.stringify({ token, expiry: this.expiry })); } catch { /* In-memory session still works when storage is unavailable. */ }
  }
  get() { if (this.now() >= this.expiry) { this.clear(); return null; } return this.token; }
  clear() { this.token = null; this.expiry = 0; try { this.storage.removeItem('emporos.session'); } catch { /* Already cleared in memory. */ } }
}
