import { defineConfig } from "@playwright/test";
import shared from "./playwright.config";

// Isolated UI fixture; all /api requests are mocked by the test. Never reuse a live stack.
process.env.INGEST_UI_BASE_URL = "http://127.0.0.1:5187";
export default defineConfig(shared, {
  testMatch: ["ingestion-unit.spec.ts", "ingestion-ui.spec.ts"],
  webServer: {
    command: "npm run dev -- --host 127.0.0.1 --port 5187 --strictPort",
    url: "http://127.0.0.1:5187/e2e/ingestion-harness.html",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
