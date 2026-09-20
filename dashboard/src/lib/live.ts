import { ApiClient, ApiError } from "./api";
import { ApiSchema, type Resource } from "./schema";

/** Parses incremental SSE frames, including CRLF, comments, and chunk boundaries. */
export class SseDecoder {
  private buffer = "";
  constructor(
    private readonly event: (data: string, id: string | undefined) => void,
  ) {}
  push(chunk: string) {
    this.buffer += chunk;
    if (this.buffer.length > 1_000_000)
      throw new Error("Live-update frame too large");
    let boundary: RegExpExecArray | null;
    while ((boundary = /\r?\n\r?\n/.exec(this.buffer))) {
      const frame = this.buffer.slice(0, boundary.index);
      this.buffer = this.buffer.slice(boundary.index + boundary[0].length);
      const data: string[] = [];
      let id: string | undefined;
      for (const line of frame.split(/\r?\n/)) {
        if (line.startsWith("data:"))
          data.push(line.slice(5).replace(/^ /, ""));
        if (line.startsWith("id:")) id = line.slice(3).trim();
      }
      if (data.length) this.event(data.join("\n"), id);
    }
  }
}
export type LiveStatus = "connecting" | "live" | "polling";
/** Uses authenticated fetch streaming; no token appears in a URL. */
export class LiveUpdates {
  private abort: AbortController | null = null;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private watchdog: ReturnType<typeof setTimeout> | null = null;
  private stopped = true;
  private generation = 0;
  private lastId = "";
  constructor(
    private readonly api: ApiClient,
    private readonly invalidate: (resource?: Resource) => void,
    private readonly status: (value: LiveStatus) => void,
    private readonly unauthorized: () => void,
  ) {}
  start() {
    this.stopped = false;
    void this.connect();
  }
  stop() {
    this.stopped = true;
    this.generation += 1;
    this.abort?.abort();
    if (this.retry) clearTimeout(this.retry);
    if (this.watchdog) clearTimeout(this.watchdog);
  }
  private heartbeat() {
    if (this.watchdog) clearTimeout(this.watchdog);
    this.watchdog = setTimeout(() => this.abort?.abort(), 25_000);
  }
  private async connect() {
    if (this.stopped) return;
    const generation = this.generation;
    const abort = new AbortController();
    this.abort = abort;
    this.status("connecting");
    this.heartbeat();
    try {
      const response = await this.api.stream(abort.signal, this.lastId);
      if (generation !== this.generation || this.stopped) return;
      const reader = response.body?.getReader();
      if (!reader) throw new Error("No stream");
      this.status("live");
      this.invalidate();
      const text = new TextDecoder();
      const parser = new SseDecoder((data, id) => {
        if (id !== undefined) this.lastId = id;
        try {
          const message: unknown = JSON.parse(data);
          if (
            typeof message === "object" &&
            message &&
            "resource" in message &&
            typeof message.resource === "string" &&
            Object.hasOwn(ApiSchema.resources, message.resource)
          )
            this.invalidate(message.resource as Resource);
          else this.invalidate();
        } catch {
          this.invalidate();
        }
      });
      while (!this.stopped && generation === this.generation) {
        const chunk = await reader.read();
        if (chunk.done || generation !== this.generation) break;
        this.heartbeat();
        parser.push(text.decode(chunk.value, { stream: true }));
      }
    } catch (error) {
      if (
        generation === this.generation &&
        !this.stopped &&
        error instanceof ApiError &&
        error.status === 401
      ) {
        this.unauthorized();
        return;
      }
    } finally {
      if (generation === this.generation && this.watchdog)
        clearTimeout(this.watchdog);
      abort.abort();
    }
    if (!this.stopped && generation === this.generation) {
      this.status("polling");
      this.retry = setTimeout(() => void this.connect(), 10_000);
    }
  }
}
