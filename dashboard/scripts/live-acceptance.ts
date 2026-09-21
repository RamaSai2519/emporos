import { spawn, type ChildProcess } from "node:child_process";
import { setTimeout } from "node:timers/promises";

/** Owns the isolated LIVE (emulator) server for one browser acceptance run, including teardown. */
class LiveAcceptanceRunner {
  private async finished(child: ChildProcess): Promise<number> {
    if (child.exitCode !== null) return child.exitCode;
    return new Promise((resolve, reject) => {
      child.once("error", reject);
      child.once("exit", (code) => resolve(code ?? 1));
    });
  }
  async run() {
    const server = spawn(
      "pipenv",
      ["run", "python", "-m", "dashboard.tests.live_server"],
      { cwd: "..", stdio: "inherit", env: { ...process.env, ENV: "local" } },
    );
    const serverDone = this.finished(server);
    try {
      let ready = false;
      for (let attempt = 0; attempt < 180; attempt++) {
        if (server.exitCode !== null)
          throw new Error("Live server exited during startup.");
        try {
          ready = (
            await fetch("http://127.0.0.1:8012/health", {
              signal: AbortSignal.timeout(1000),
            })
          ).ok;
        } catch {
          /* Waiting for the real database-backed composition. */
        }
        if (ready) break;
        await setTimeout(1000);
      }
      if (!ready) throw new Error("Live acceptance server did not start.");
      const browser = spawn("npx", ["playwright", "test"], {
        stdio: "inherit",
        env: { ...process.env, EMPOROS_LIVE_E2E: "1" },
      });
      if ((await this.finished(browser)) !== 0)
        throw new Error("Live browser acceptance failed.");
    } finally {
      server.kill("SIGTERM");
      await serverDone;
    }
  }
}
await new LiveAcceptanceRunner().run();
