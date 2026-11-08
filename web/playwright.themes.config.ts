import { defineConfig } from '@playwright/test';

// `make theme-shots THEME=<id>`: renders one theme (or THEME=all) at every zoom level plus its slot sheet into
// docs/screenshots/themes/<id>/. Writes images for review; it compares nothing (conformance is a vitest test).
const port = Number(process.env.RACKWALKER_THEME_PORT ?? 5342);

export default defineConfig({
  testDir: 'e2e-themes',
  outputDir: 'test-results-themes',
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  reporter: [['list']],
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
