import { readFile, writeFile } from 'node:fs/promises';
import { z } from 'zod';
import openapiTS, { astToString } from 'openapi-typescript';
import { ApiSchema } from '../src/lib/schema';

/** Generates the provisional frontend contract; verifies it against the API when available. */
class ContractTool {
  private response(schema: z.ZodType) {
    return { description: 'Success', content: { 'application/json': { schema: z.toJSONSchema(schema, { unrepresentable: 'any' }) } } };
  }
  private document() {
    const paths: Record<string, unknown> = {};
    for (const [name, schema] of Object.entries(ApiSchema.resources)) paths[`/api/${name}`] = { get: { responses: { '200': this.response(schema) } } };
    paths['/auth/login'] = { post: { security: [], requestBody: { required: true, content: { 'application/json': { schema: { type: 'object', required: ['passcode'], properties: { passcode: { type: 'string' } } } } } }, responses: { '200': this.response(ApiSchema.login) } } };
    paths['/api/commands'] = { post: { requestBody: { required: true, content: { 'application/json': { schema: z.toJSONSchema(ApiSchema.commandInput, { unrepresentable: 'any' }) } } }, responses: { '202': this.response(ApiSchema.command) } } };
    paths['/api/commands/{idempotency_key}'] = { get: { parameters: [{ name: 'idempotency_key', in: 'path', required: true, schema: { type: 'string' } }], responses: { '200': this.response(ApiSchema.command) } } };
    paths['/api/candles'] = { get: { parameters: [{ name: 'instrument_id', in: 'query', required: true, schema: { type: 'string' } }, { name: 'interval', in: 'query', required: true, schema: { type: 'string', enum: ['1m', '5m', '15m'] } }], responses: { '200': this.response(ApiSchema.candles) } } };
    paths['/api/events'] = { get: { responses: { '200': { description: 'SSE invalidations; data: {"resource":"orders"}. Heartbeat every <=15 seconds.', content: { 'text/event-stream': { schema: { type: 'string' } } } } } } };
    return { openapi: '3.1.0', info: { title: 'Emporos frontend integration proposal — pending EM-40', version: '0.1.0' }, security: [{ bearerAuth: [] }], components: { securitySchemes: { bearerAuth: { type: 'http', scheme: 'bearer' } } }, paths };
  }
  async run(mode: string | undefined) {
    const document = JSON.stringify(this.document(), null, 2) + '\n';
    const types = astToString(await openapiTS(JSON.parse(document)));
    if (mode === 'generate') {
      await writeFile('contracts/openapi.json', document);
      await writeFile('src/lib/api.generated.ts', types);
      return;
    }
    if (document !== await readFile('contracts/openapi.json', 'utf8') || types !== await readFile('src/lib/api.generated.ts', 'utf8')) throw new Error('Contract artifacts drifted. Run npm run contract:generate and review the changes.');
    if (mode === 'verify') {
      const url = process.env.EMPOROS_OPENAPI_URL;
      if (!url) throw new Error('EMPOROS_OPENAPI_URL must point to the running EM-40 API.');
      const response = await fetch(url);
      if (!response.ok) throw new Error(`Schema unavailable: ${response.status}`);
      const actual = astToString(await openapiTS(await response.json()));
      if (actual !== types) throw new Error('Backend schema differs from the checked-in frontend contract. Regenerate and reconcile before integration.');
    }
  }
}
await new ContractTool().run(process.argv[2]);
