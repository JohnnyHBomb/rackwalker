// Regression: the dev server must not serve repo files outside web/ and fixtures/ (it served the
// dev NetBox secrets in devnetbox/.stacks/ with ACAO for any localhost origin while fs.allow was '..').
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { createServer, type ViteDevServer } from 'vite';

const web = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repo = resolve(web, '..');
let server: ViteDevServer;
let base = '';

beforeAll(async () => {
  server = await createServer({ configFile: resolve(web, 'vite.config.ts'), root: web, logLevel: 'silent',
    server: { port: 0, strictPort: false, host: '127.0.0.1' } });
  await server.listen();
  const addr = server.httpServer!.address();
  base = `http://127.0.0.1:${typeof addr === 'object' && addr ? addr.port : 0}`;
}, 30_000);
afterAll(async () => { await server?.close(); });

const status = async (path: string) => (await fetch(`${base}/@fs${resolve(repo, path)}`)).status;

describe('vite dev server file access (server.fs.allow)', () => {
  it('serves fixtures/, which the fixture source bundles', async () => {
    expect(await status('fixtures/index.json')).toBe(200);
  });
  it.each(['README.md', 'devnetbox/README.md', 'devnetbox/compose.yml', 'proxy/pyproject.toml', 'Makefile'])('refuses %s', async (p) => {
    expect(await status(p)).toBe(403);
  });
});

// Regression: Vite's default server.cors answered any http://localhost:* origin, also on the
// proxied /api, so a page on another local port could read every scene and POST /api/changes/apply (measured: a
// write went through on the proxy's rw token). With cors off no response carries Access-Control-Allow-Origin, and
// the preflight of a JSON POST goes on to the proxy, which does not answer OPTIONS (405), so the browser stops there.
describe('vite dev server CORS (server.cors)', () => {
  const evil = 'http://localhost:9999';
  it('a preflight from another local origin gets no Access-Control-Allow-Origin (also on /api)', async () => {
    for (const path of ['/api/changes/apply', '/api/scene/world', '/']) {
      const r = await fetch(`${base}${path}`, {
        method: 'OPTIONS',
        headers: { Origin: evil, 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type' },
      });
      expect(r.headers.get('access-control-allow-origin'), path).toBeNull();
    }
  });
  it('a page served from another local origin gets no Access-Control-Allow-Origin on a read', async () => {
    const r = await fetch(`${base}/index.html`, { headers: { Origin: evil } });
    expect(r.status).toBe(200);
    expect(r.headers.get('access-control-allow-origin')).toBeNull();
  });
});

// No other site may frame the game or the editor: a framed editor could be steered into a drag and an Apply.
describe('vite dev server framing', () => {
  it('every page says it must not be framed', async () => {
    for (const path of ['/', '/sheet.html', '/?view=rack:1&game=0']) {
      const r = await fetch(`${base}${path}`);
      expect(r.headers.get('x-frame-options')).toBe('DENY');
      expect(r.headers.get('content-security-policy')).toBe("frame-ancestors 'none'");
    }
  });
});
