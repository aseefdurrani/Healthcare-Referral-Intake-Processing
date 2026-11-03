import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  use: {
    ...devices["Desktop Chrome"],
    baseURL: "http://127.0.0.1:3000",
  },
  webServer: {
    command: "pnpm build && pnpm start --hostname 127.0.0.1 --port 3000",
    url: "http://127.0.0.1:3000/sign-in",
    timeout: 180_000,
    reuseExistingServer: false,
    env: {
      AUTH_MODE: "mock",
      NEXT_PUBLIC_APP_ENV: "test",
      AMPLIFY_APP_ORIGIN: "http://127.0.0.1:3000",
      BACKEND_INTERNAL_URL: "http://127.0.0.1:8000",
      NEXT_TELEMETRY_DISABLED: "1",
    },
  },
});
