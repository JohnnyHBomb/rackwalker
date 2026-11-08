import { defineConfig } from '@playwright/test';

// Golden screenshots + interaction + perf, headless chromium. Browsers live in PLAYWRIGHT_BROWSERS_PATH
// (make test-e2e points it at <repo>/.ms-playwright). Own port, so it never collides with `make dev`.
const port = Number(process.env.RACKWALKER_E2E_PORT ?? 5341);

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
  webServer: {
    command: `npx vite --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}/`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
