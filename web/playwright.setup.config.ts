import { defineConfig } from '@playwright/test';

// The setup companion in the browser, without Docker and without a network: a made-up NetBox that starts empty
// (proxy/tests/empty_netbox.py), a real write-enabled proxy in front of it with a fresh game store, and vite.
// Imports read the few library files in proxy/tests/library; fetching is forbidden. Own ports, so it runs beside
// `make dev` and the other e2e. Screenshots go to test-results/ (RACKWALKER_SETUP_SHOTS=1: to docs/screenshots/setup/).
const port = Number(process.env.RACKWALKER_SETUP_PORT ?? 5343);
const proxyPort = Number(process.env.RACKWALKER_SETUP_PROXY_PORT ?? 8743);
const boxPort = Number(process.env.RACKWALKER_SETUP_NETBOX_PORT ?? 8745);
const venv = process.env.VENV ?? '../.venv';
const state = `../data/e2e-setup-${Date.now()}`;

export default defineConfig({
  testDir: 'e2e-setup',
  outputDir: 'test-results',
  fullyParallel: false,
  workers: 1,
  timeout: 240_000,
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${port}`, browserName: 'chromium', headless: true, viewport: { width: 800, height: 600 }, deviceScaleFactor: 1 },
  webServer: [
    {
      command: `${venv}/bin/python -m tests.empty_netbox ${boxPort}`,
      cwd: '../proxy',
      url: `http://127.0.0.1:${boxPort}/api/status/`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `${venv}/bin/uvicorn rackwalker_proxy.app:create_app --factory --host 127.0.0.1 --port ${proxyPort}`,
      url: `http://127.0.0.1:${proxyPort}/api/packs`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        NETBOX_URL: `http://127.0.0.1:${boxPort}`, NETBOX_TOKEN: 'nbt_synthetic.not-a-secret-proxy', WRITE_ENABLED: '1', RACKWALKER_STATE_DIR: state,
        RACKWALKER_LIBRARY_DIR: '../proxy/tests/library', RACKWALKER_LIBRARY_FETCH: '0', RACKWALKER_CACHE_TTL: '0',
      },
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
