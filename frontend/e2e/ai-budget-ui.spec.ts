import { test, expect } from "@playwright/test";

const base = process.env.INGEST_UI_BASE_URL;
test.skip(!base, "Solo servidor Vite aislado, nunca proveedores ni datos reales");

test("confirmar un perfil exige tarifas explícitas y se invalida al modificar sus límites", async ({ page }) => {
  let saved: any = null;
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    if (route.request().method() === "PUT" && route.request().url().includes("/providers/bindings")) {
      saved = route.request().postDataJSON();
      return route.fulfill({ json: { ...saved, id: "binding" } });
    }
    return route.fulfill({ json: [] });
  });
  await page.goto(`${base}/e2e/ai-budget-harness.html`);
  await page.getByText("Tarifa y límites de IA pendientes de confirmar", { exact: true }).click();
  const submit = page.getByRole("button", { name: "Confirmar perfil de IA" });
  await expect(submit).toBeDisabled();
  await expect(page.getByLabel("Techo entrada")).toHaveValue("");
  await page.getByLabel("Ventana máxima del proveedor").fill("100000");
  await page.getByLabel("Techo entrada").fill("0.123456");
  await page.getByLabel("Techo salida").fill("1.234567");
  await page.getByLabel("Revisar antes de").fill("2099-01-01");
  await page.getByLabel("Fuente de tarifa y contexto").fill("Synthetic test source, not actual prices");
  const confirmation = page.getByLabel("He comprobado el modelo");
  await confirmation.check();
  await page.getByLabel("Salida máxima").fill("1000");
  await expect(confirmation).not.toBeChecked();
  await confirmation.check(); await submit.click();
  await expect(page.getByRole("status").first()).toContainText("Perfil confirmado");
  expect(saved.params.budget_policy.input_usd_per_million).toBe("0.123456");
  expect(saved.params.budget_policy.context_tokens).toBe(100000);
  expect(saved.params.budget_policy.max_output_tokens).toBe(1000);
  expect(saved.params.budget_policy.valid_until).toBe("2099-01-01T00:00:00Z");
  expect(saved).not.toHaveProperty("api_key");
});

test("una llamada incierta no se libera sin importe y evidencia confirmados", async ({ page }) => {
  let reconciled: any = null;
  const call = { id: "call", state: "unknown", profile: { model: "synthetic" }, reserved_microusd: 100000,
    cost_microusd: null, provider_request_id: "req-test", created_at: "2026-01-01T00:00:00Z" };
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    if (route.request().method() === "POST") {
      expect(route.request().url()).toContain("/ai-calls/call/reconcile");
      reconciled = route.request().postDataJSON();
      return route.fulfill({ json: { ...call, state: "reconciled" } });
    }
    return route.fulfill({ json: reconciled ? [] : [call] });
  });
  await page.goto(`${base}/e2e/ai-budget-harness.html`);
  await page.getByText("Registro de llamadas y conciliación (administración)", { exact: true }).click();
  await page.getByRole("button", { name: "Conciliar con evidencia" }).click();
  const submit = page.getByRole("button", { name: "Guardar conciliación" });
  await expect(submit).toBeDisabled();
  await expect(page.getByLabel("Coste verificado (USD)")).toHaveValue("");
  await page.getByLabel("Coste verificado (USD)").fill("0.012345");
  await page.getByLabel("Evidencia de facturación").fill("Provider test invoice confirmed request req-test");
  await page.getByLabel("He verificado el importe").check(); await submit.click();
  await expect(page.getByRole("status").last()).toContainText("Conciliación guardada");
  expect(reconciled.cost_microusd).toBe(12345);
  expect(reconciled.provider_request_id).toBe("req-test");
  await expect(page.getByRole("button", { name: "Conciliar con evidencia" })).toHaveCount(0);
});
