import { ApiError, type CommandGateway } from "./api";
import {
  ApiSchema,
  type Command,
  type CommandDraft,
  type CommandInput,
} from "./schema";

export type CommandState = Readonly<{
  input: CommandInput | null;
  result: Command | null;
  unresolved: boolean;
  busy: boolean;
  error: string | null;
}>;
/** Persists intent before HTTP and resolves ambiguous outcomes without generating a new key. */
export class CommandCoordinator {
  private knownId: string | undefined;
  private current: CommandState = {
    input: null,
    result: null,
    unresolved: false,
    busy: false,
    error: null,
  };
  constructor(
    private readonly gateway: CommandGateway,
    private readonly storage: Storage,
    private readonly uuid: () => string,
    private readonly changed: (state: CommandState) => void,
  ) {}
  get state() {
    return this.current;
  }
  get pending() {
    return (
      this.current.busy ||
      this.current.unresolved ||
      (!!this.current.result && !this.terminal(this.current.result))
    );
  }
  restore() {
    this.knownId = this.storage.getItem("emporos.command.id") ?? undefined;
    const value = this.storage.getItem("emporos.command");
    if (!value) return;
    try {
      this.update({
        input: ApiSchema.commandInput.parse(JSON.parse(value)),
        result: null,
        unresolved: true,
        busy: false,
        error: "Restored command. Checking its durable outcome.",
      });
    } catch {
      this.update({
        ...this.current,
        unresolved: true,
        error:
          "Saved command is unreadable. Review the command audit log before clearing browser storage.",
      });
    }
  }
  async submit(draft: CommandDraft) {
    if (this.pending) return;
    const input = ApiSchema.commandInput.parse({
      ...draft,
      idempotency_key: this.uuid(),
    });
    // Storage failure must fail closed: a reload must never erase an uncertain submission.
    try {
      this.storage.setItem("emporos.command", JSON.stringify(input));
      this.knownId = undefined;
      this.storage.removeItem("emporos.command.id");
    } catch {
      this.update({
        ...this.current,
        error:
          "Browser storage is unavailable. Commands are disabled to preserve restart safety.",
      });
      return;
    }
    this.update({
      input,
      result: null,
      busy: true,
      unresolved: true,
      error: null,
    });
    try {
      this.accept(await this.gateway.submit(input));
    } catch (error) {
      if (
        error instanceof ApiError &&
        [400, 401, 403, 422, 429].includes(error.status)
      ) {
        this.storage.removeItem("emporos.command");
        this.update({
          ...this.current,
          busy: false,
          unresolved: false,
          error: `Request refused (${error.status}). ${error.message}`,
        });
        return;
      }
      this.update({
        ...this.current,
        busy: false,
        unresolved: true,
        error:
          error instanceof ApiError && error.status === 401
            ? error.message
            : "Delivery is unconfirmed. Checking the original command; do not submit it again.",
      });
    }
  }
  async resolve() {
    if (!this.current.input || !this.pending || this.current.busy) return;
    this.update({ ...this.current, busy: true });
    try {
      this.accept(
        await this.gateway.command(
          this.current.input.idempotency_key,
          this.knownId,
        ),
      );
    } catch (error) {
      this.update({
        ...this.current,
        busy: false,
        error:
          error instanceof ApiError && error.status === 404
            ? "Command not found yet. Delivery remains unconfirmed; review the system audit before further actions."
            : "Cannot confirm the command outcome. Reconnecting…",
      });
    }
  }
  private terminal(command: Command) {
    return ["DONE", "FAILED", "REJECTED", "EXPIRED"].includes(command.status);
  }
  private accept(result: Command) {
    if (result.idempotency_key !== this.current.input?.idempotency_key)
      throw new Error("Command identity mismatch");
    this.knownId = result.id;
    this.storage.setItem("emporos.command.id", result.id);
    if (this.terminal(result)) this.storage.removeItem("emporos.command");
    this.update({
      ...this.current,
      result,
      busy: false,
      unresolved: false,
      error: null,
    });
  }
  private update(state: CommandState) {
    this.current = state;
    this.changed(state);
  }
}
