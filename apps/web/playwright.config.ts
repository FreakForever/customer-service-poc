import { defineConfig } from "@playwright/test";

/** Needs `make proxy` and .env. Starts the scripted agent API on mcs_test and a production build of the web app. */
const secret = "e2e-secret";
export default defineConfig({
  testDir: "e2e", timeout: 60_000, workers: 1, retries: 0, reporter: "line",
  expect: { timeout: 15_000 },
  use: { baseURL: "http://localhost:3100", viewport: { width: 1366, height: 768 }, trace: "retain-on-failure" },
  webServer: [
    { command: "cd ../../services/agent && uv run python ../../scripts/e2e_server.py --port 8100", url: "http://127.0.0.1:8100/health", timeout: 180_000, env: { E2E_SECRET: secret }, stdout: "ignore", stderr: "pipe" },
    { command: "pnpm build && cp -r .next/static .next/standalone/.next/ && node .next/standalone/server.js", url: "http://localhost:3100/api/health", timeout: 180_000,
      env: { PORT: "3100", HOSTNAME: "localhost", AGENT_API_URL: "http://127.0.0.1:8100", NEXTAUTH_SECRET: secret, NEXTAUTH_URL: "http://localhost:3100", ENV: "dev", DEMO_STAFF_LOGIN: "true" } },
  ],
});
