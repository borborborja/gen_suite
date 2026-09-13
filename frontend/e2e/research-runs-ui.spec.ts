import { test, expect } from "@playwright/test";

const base = process.env.INGEST_UI_BASE_URL;
test.skip(!base, "Solo servidor Vite aislado, nunca proveedores ni datos reales");

test("preparar no inicia IA y pausar/reanudar conserva la misma investigación", async ({ page }) => {
  let run: any = null;
  let prepared: any = null;
  const controls: { id: string; action: string; version: number }[] = [];
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (path === "/api/documents") return route.fulfill({ json: [{ id: "book", title: "Bautismos sintéticos", page_count: 12 }] });
    if (path === "/api/research/projects") return route.fulfill({ json: [] });
    if (path === "/api/research/runs" && request.method() === "POST") {
      prepared = request.postDataJSON();
      run = { id: "stable-run", title: prepared.title, status: "planned", version: 1, budget_microusd: prepared.budget_microusd,
        spent_microusd: 0, reserved_microusd: 0, stage_counts: { pending: 1 }, document_count: 1, person_count: 0,
        profiles: { transcription: { engine: "test", model: "frozen-test-model" } }, config: {}, error: null };
      return route.fulfill({ status: 201, json: run });
    }
    if (path === "/api/research/runs") return route.fulfill({ json: run ? [run] : [] });
    if (path.endsWith("/events")) return route.fulfill({ json: [] });
    if (path.endsWith("/stages")) return route.fulfill({ json: [{ id: "stable-stage", position: 0, kind: "transcribe", title: "Libro",
      status: run.status === "planned" ? "pending" : run.status, job_id: run.status === "planned" ? null : "same-child-job", attempts: controls.length, result: {} }] });
    if (path === "/api/research/runs/stable-run") return route.fulfill({ json: run });
    if (request.method() === "POST") {
      const action = path.split("/").at(-1)!;
      controls.push({ id: path.split("/").at(-2)!, action, version: request.postDataJSON().expected_version });
      expect(request.postDataJSON().expected_version).toBe(run.version);
      run = { ...run, version: run.version + 1, status: action === "pause" ? "paused" : "running" };
      return route.fulfill({ json: run });
    }
    throw new Error(`Petición no prevista: ${request.method()} ${path}`);
  });
  await page.goto(`${base}/e2e/research-runs-harness.html`);
  await page.getByRole("button", { name: "Preparar investigación", exact: true }).click();
  await page.getByLabel("Título", { exact: true }).fill("Familia de prueba");
  await page.getByLabel("Presupuesto total (USD)").fill("1.234567");
  await page.getByLabel("Bautismos sintéticos").check();
  await page.getByRole("button", { name: "Crear plan sin llamadas de IA" }).click();
  await expect(page.getByRole("status")).toContainText("Plan preparado sin llamadas de IA");
  expect(prepared.budget_microusd).toBe(1234567);
  expect(prepared.document_ids).toEqual(["book"]);
  expect(controls).toEqual([]);
  const start = page.getByRole("button", { name: "Iniciar con este presupuesto" });
  await expect(start).toBeDisabled();
  await page.getByText("Modelos fijados para esta investigación", { exact: true }).click();
  await expect(page.getByText("Transcripción: test / frozen-test-model")).toBeVisible();
  await page.getByLabel("Autorizo iniciar las etapas").check(); await start.click();
  await page.getByRole("button", { name: "Pausar", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Las llamadas ya enviadas aún pueden terminar y facturarse");
  await page.reload();
  await page.getByRole("button", { name: "Familia de prueba · Pausada" }).click();
  await page.getByRole("button", { name: "Reanudar misma investigación" }).click();
  await expect(page.getByRole("heading", { name: "Familia de prueba · En marcha" })).toBeVisible();
  expect(controls).toEqual([
    { id: "stable-run", action: "start", version: 1 },
    { id: "stable-run", action: "pause", version: 2 },
    { id: "stable-run", action: "resume", version: 3 },
  ]);
});

test("una revisión obsoleta no muestra éxito ni cambia el presupuesto", async ({ page }) => {
  const run = { id: "conflict", title: "Revisión", status: "needs_attention", version: 7, budget_microusd: 1000000,
    spent_microusd: 250000, reserved_microusd: 750000, stage_counts: { needs_attention: 1 }, document_count: 1,
    person_count: 0, profiles: {}, config: {}, error: "Revisar consumo" };
  let requested: any = null;
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "PATCH") {
      requested = route.request().postDataJSON();
      return route.fulfill({ status: 409, json: { detail: "La investigación ha cambiado; actualiza su estado antes de continuar." } });
    }
    return route.fulfill({ json: path.endsWith("/runs") ? [run] : path.endsWith("/conflict") ? run : [] });
  });
  await page.goto(`${base}/e2e/research-runs-harness.html`);
  await page.getByRole("button", { name: "Revisión · Necesita atención" }).click();
  await page.getByText("Resolver incidencias / presupuesto", { exact: true }).click();
  await page.getByLabel("Nuevo presupuesto (USD)").fill("2.000001");
  const submit = page.getByRole("button", { name: "Actualizar presupuesto con motivo" });
  await expect(submit).toBeDisabled();
  await page.getByLabel("Motivo o corrección realizada").fill("Ampliación verificada en prueba sintética"); await submit.click();
  await expect(page.getByRole("status")).toContainText("La investigación ha cambiado");
  expect(requested.expected_version).toBe(7); expect(requested.budget_microusd).toBe(2000001);
  await expect(page.getByText(/Liquidado estimado:/)).toContainText("presupuesto: $1.000000");
});
