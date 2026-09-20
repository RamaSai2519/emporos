import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";

/** Prevents broker/storage SDKs, endpoints and credentials from entering frontend code or bundles. */
class FrontendBoundaryCheck {
  private readonly forbidden = [
    /smartapi/i,
    /angelbroking/i,
    /angelone/i,
    /mongodb(?:\+srv)?:/i,
    /(?:from\s*|import\s*\()['"](?:mongodb|mongoose|@aws-sdk|aws-sdk)/,
    /(?:ANGEL|BROKER)_(?:API_KEY|SECRET|PASSWORD|PIN|TOKEN)/,
  ];
  async scan(directory: string) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) await this.scan(path);
      else if (/\.(?:ts|tsx|js|mjs)$/.test(entry.name)) {
        const source = await readFile(path, "utf8");
        if (this.forbidden.some((rule) => rule.test(source)))
          throw new Error(`Frontend boundary violation in ${path}`);
      }
    }
  }
  async run() {
    await this.scan("src");
    try {
      await readdir(".next/static");
    } catch {
      return;
    }
    await this.scan(".next/static");
  }
}
await new FrontendBoundaryCheck().run();
