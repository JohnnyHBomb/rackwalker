import { defineConfig } from '@playwright/test';

// Golden screenshots + interaction + perf, headless chromium. Browsers live in PLAYWRIGHT_BROWSERS_PATH
// (make test-e2e points it at <repo>/.ms-playwright). Own port, so it never collides with `make dev`.
const port = Number(process.env.RACKWALKER_E2E_PORT ?? 5341);
// The game e2e (e2e/game.spec.ts) needs a real proxy for its sign-in and game store: fixture mode (no NetBox), with a
// fresh state directory per run under the gitignored data/.
const proxyPort = Number(process.env.RACKWALKER_E2E_PROXY_PORT ?? 8741);
const venv = process.env.VENV ?? '../.venv';

export default defineConfig({
  testDir: 'e2e',
  outputDir: 'test-results',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  reporter: [['list']],
  expect: { toHaveScreenshot: { maxDiffPixels: 0 } },
  snapshotPathTemplate: '{testDir}/__screenshots__/{arg}{ext}',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    browserName: 'chromium',
    headless: true,
    viewport: { width: 800, height: 600 },
    deviceScaleFactor: 1,
  },
  webServer: [
    {
      command: `rm -rf ../data/e2e-game && RACKWALKER_FIXTURES=1 RACKWALKER_STATE_DIR=../data/e2e-game ${venv}/bin/uvicorn rackwalker_proxy.app:create_app --factory --host 127.0.0.1 --port ${proxyPort}`,
      url: `http://127.0.0.1:${proxyPort}/api/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npx vite --host 127.0.0.1 --port ${port} --strictPort`,
      url: `http://127.0.0.1:${port}/`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { RACKWALKER_PROXY_URL: `http://127.0.0.1:${proxyPort}` },
    },
  ],
});
