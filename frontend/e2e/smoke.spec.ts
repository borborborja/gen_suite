import { expect, test } from "@playwright/test";

test.skip(!process.env.E2E_BASE_URL, "requires the disposable Compose E2E stack");

test("the production web entrypoint and readiness endpoint are reachable", async ({ page, request }) => {
  const ready = await request.get("/readyz");
  expect(ready.ok()).toBeTruthy();
  await page.goto("/");
  await expect(page).toHaveTitle(/Estela|gen_suite/i);
});
