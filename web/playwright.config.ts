import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const dataDir =
  process.env.BOOKCAST_E2E_DATA ?? mkdtempSync(join(tmpdir(), "bookcast-e2e-"));
process.env.BOOKCAST_E2E_DATA = dataDir;
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  workers: 1,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:8877",
    browserName: "chromium",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: `../.venv/bin/bookcast serve --port 8877 --data-dir "${dataDir}" --ui-dir "${resolve("out")}"`,
    url: "http://127.0.0.1:8877/api/health",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
