import { readFile, writeFile } from "node:fs/promises";
import openapiTS, { astToString } from "openapi-typescript";

/** Generates the client contract from the actual FastAPI export and rejects drift. */
class ContractTool {
  private canonical(value: unknown): string {
    if (Array.isArray(value))
      return `[${value.map((item) => this.canonical(item)).join(",")}]`;
    if (value !== null && typeof value === "object")
      return `{${Object.entries(value)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, item]) => `${JSON.stringify(key)}:${this.canonical(item)}`)
        .join(",")}}`;
    return JSON.stringify(value);
  }
  async run(mode: string | undefined) {
    const document = JSON.parse(
      await readFile("contracts/openapi.json", "utf8"),
    );
    const official = JSON.parse(
      await readFile("../docs/api/openapi.json", "utf8"),
    );
    if (this.canonical(document) !== this.canonical(official))
      throw new Error(
        "Frontend schema differs from docs/api/openapi.json. Export the real API and regenerate.",
      );
    const types = astToString(
      await openapiTS(document, { emptyObjectsUnknown: true }),
    );
    if (mode === "generate") {
      await writeFile("src/lib/api.generated.ts", types);
      return;
    }
    if (types !== (await readFile("src/lib/api.generated.ts", "utf8")))
      throw new Error(
        "Generated API types are stale. Export the backend schema, regenerate and review.",
      );
    if (mode === "verify" || process.env.EMPOROS_OPENAPI_URL) {
      const url = process.env.EMPOROS_OPENAPI_URL;
      if (!url)
        throw new Error("Set EMPOROS_OPENAPI_URL to the running control API.");
      const response = await fetch(url);
      if (!response.ok)
        throw new Error(`Schema unavailable: ${response.status}`);
      const actual = astToString(
        await openapiTS(await response.json(), { emptyObjectsUnknown: true }),
      );
      if (actual !== types)
        throw new Error(
          "Backend OpenAPI changed. Export, regenerate and reconcile the client before building.",
        );
    }
  }
}
await new ContractTool().run(process.argv[2]);
