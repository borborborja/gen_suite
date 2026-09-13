import { defineConfig } from "@playwright/test";

/**
 * CI supplies E2E_BASE_URL from a disposable Compose project.  Keeping the browser runner
 * detached from `npm run dev` means it can never accidentally exercise a developer's live
 * stack or its persisted data volume.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL,
    browserName: "chromium",
    ignoreHTTPSErrors: true, // self-signed certificate in the disposable CI proxy only
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
