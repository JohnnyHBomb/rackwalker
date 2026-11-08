import { defineConfig } from '@playwright/test';

// Live end-to-end check (not part of make test / test-e2e): drives the app served by `make dev`, which proxies
// /api to the FastAPI proxy reading the dev NetBox. Run with `make e2e-live` while `make dev` is up.
const base = process.env.RACKWALKER_LIVE_URL ?? 'http://127.0.0.1:5173';

export default defineConfig({
  testDir: 'e2e-live',
  outputDir: 'test-results-live',
  workers: 1,
  timeout: 120_000,
  reporter: [['list']],
  use: {
    baseURL: base,
    browserName: 'chromium',
    headless: true,
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 1,
  },
});
