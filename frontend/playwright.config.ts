import { defineConfig } from '@playwright/test';

const python = process.env.USI_PYTHON ?? (process.platform === 'win32' ? '../backend/.venv/Scripts/python.exe' : 'python');

export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  retries: process.env.CI ? 1 : 0,
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: [
    {
      command: `${python} -m uvicorn app.main:app --host 127.0.0.1 --port 8000`,
      cwd: '../backend',
      url: 'http://127.0.0.1:8000/api/health',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        USI_RUN_WORKER_IN_API: 'true',
        USI_DATA_DIR: process.env.USI_E2E_DATA_DIR ?? './data-e2e',
        USI_DEMO_MODE: 'true',
        USI_AUTH_MODE: 'local',
      },
    },
    {
      command: 'npm run dev -- --host 127.0.0.1',
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
