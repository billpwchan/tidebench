import { defineConfig, devices } from '@playwright/test';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('.', import.meta.url)), '..');
const port = Number(process.env.TIDEBENCH_E2E_PORT ?? '8100');
if (!Number.isInteger(port) || port < 1024 || port > 65535)
  throw new Error('TIDEBENCH_E2E_PORT must be a TCP port from 1024 to 65535.');
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'desktop',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 1000 } },
    },
    { name: 'mobile', use: { ...devices['iPhone 13'], defaultBrowserType: 'chromium' } },
  ],
  webServer: {
    command: '.venv/bin/python -m tidebench',
    cwd: root,
    url: `${baseURL}/healthz`,
    reuseExistingServer: false,
    timeout: 30_000,
    env: {
      TIDEBENCH_PORT: String(port),
      TIDEBENCH_DATA_DIR: join(tmpdir(), `tidebench-browser-test-${process.pid}`),
      TIDEBENCH_API_TOKEN: '',
      TIDEBENCH_ALLOWED_HOSTS: '127.0.0.1,localhost',
      TIDEBENCH_ALLOWED_ORIGINS: baseURL,
    },
  },
});
