import { defineConfig } from 'vitest/config';

// Dev: vite serves the app and proxies /api to the FastAPI proxy (make dev starts both).
const proxyTarget = process.env.RACKWALKER_PROXY_URL ?? 'http://127.0.0.1:8710';

export default defineConfig({
  // fs.allow: web/ itself and ../fixtures (FixtureSceneSource bundles ../fixtures/*.json), nothing else of the repo:
  // '..' served devnetbox/.stacks/*.env (dev NetBox secrets) to any localhost origin.
  // cors: false. Vite's default allowed any http://localhost:* / 127.0.0.1:* origin, also on the proxied /api, so a page
  // on another local port could read the scenes and POST /api/changes/apply on the proxy's write token (measured).
  // The app itself is same-origin and needs no CORS.
  server: { host: '127.0.0.1', port: 5173, strictPort: true, cors: false, proxy: { '/api': proxyTarget }, fs: { allow: ['.', '../fixtures'] } },
  // Two pages: the app and the theme slot sheet (sheet.html?theme=<id>).
  build: { target: 'es2022', assetsInlineLimit: 0, rollupOptions: { input: { main: 'index.html', sheet: 'sheet.html' } } },
  test: { include: ['tests/**/*.test.ts'], environment: 'node' },
});
