import { test, expect } from "@playwright/test";

const base = process.env.INGEST_UI_BASE_URL;
test.skip(!base, "Solo API simulada en servidor Vite aislado");
const before = { model: "synthetic", context_tokens: 1000, max_output_tokens: 100, input_usd_per_million: "1", output_usd_per_million: "2",
  valid_until: "2000-01-01T00:00:00Z", source: "Synthetic original tariff", output_parameter: "max_completion_tokens", response_format: "json_schema" };
const owner = { scope: "research_run", id: "00000000-0000-0000-0000-000000000002", title: "Investigación de prueba", status: "paused", version: 3 };

for (const stale of [false, true]) {
  test(stale ? "rechaza una confirmación obsoleta y exige nueva comparación" : "renueva con confirmación explícita sin reanudar y descarta comparaciones al editar", async ({ page }) => {
    let approved: any = null; let previews = 0; const posts: string[] = [];
    await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
      const url = new URL(route.request().url());
      if (route.request().method() === "GET") return route.fulfill({ json: { owner, execution_profile: { engine: "openai", model: "synthetic" },
        current_policy: approved ? { ...before, ...approved } : before, authorization_version: approved ? 1 : 0, expired: !approved, history: [] } });
      posts.push(url.pathname);
      if (url.pathname.endsWith("/preview")) {
        previews += 1;
        return route.fulfill({ json: { owner, task_type: "inference", authorization_version: 0, preview_hash: "a".repeat(64), before,
          after: { ...before, ...route.request().postDataJSON() }, before_reservation_microusd: 1200, after_reservation_microusd: 3200 } });
      }
      if (stale) return route.fulfill({ status: 409, json: { detail: "La ejecución o autorización ha cambiado. Revisa de nuevo la comparación antes de confirmar." } });
      approved = route.request().postDataJSON();
      return route.fulfill({ status: 201, json: { id: "authorization", version: 1 } });
    });
    await page.goto(`${base}/e2e/authorizations-harness.html`);
    await page.getByText("Renovar autorización económica de interpretación (administración)", { exact: true }).click();
    await expect(page.getByText("CADUCADA", { exact: false })).toBeVisible();
    await page.getByLabel("Nueva revisión antes de").fill("2099-01-01");
    await page.getByLabel("Nueva tarifa máxima de entrada").fill("3");
    await page.getByLabel("Motivo de la renovación").fill("Synthetic tariff review");
    await page.getByRole("button", { name: "Comparar antes de autorizar" }).click();
    const submit = page.getByRole("button", { name: "Autorizar renovación sin reanudar" });
    await expect(submit).toBeDisabled();
    await page.getByLabel("He verificado las nuevas tarifas").check();
    await page.getByLabel("Fuente verificada de la nueva tarifa").fill("Synthetic updated tariff source");
    await expect(submit).toHaveCount(0);
    await page.getByRole("button", { name: "Comparar antes de autorizar" }).click();
    await expect(submit).toBeDisabled();
    await page.getByLabel("He verificado las nuevas tarifas").check();
    await submit.click();
    if (stale) {
      await expect(page.getByRole("status")).toContainText("ha cambiado");
      await expect(submit).toHaveCount(0); expect(approved).toBeNull();
    } else {
      await expect(page.getByRole("status")).toContainText("Autorización guardada");
      expect(approved.authorize).toBe(true); expect(approved.preview_hash).toBe("a".repeat(64));
      expect(approved.input_usd_per_million).toBe("3"); expect(approved).not.toHaveProperty("model");
      expect(approved).not.toHaveProperty("api_key");
    }
    expect(previews).toBe(2);
    expect(posts.every((path) => path.startsWith("/api/providers/authorizations"))).toBe(true);
  });
}
