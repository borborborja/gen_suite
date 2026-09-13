import { test, expect } from "@playwright/test";

const base = process.env.INGEST_UI_BASE_URL;
test.skip(!base, "Solo Vite y respuestas sintéticas; nunca la instalación real");

test("construir otra generación no sustituye el índice y activarla conserva el anterior", async ({ page }) => {
  let active = "old";
  const generations: Record<string, any> = {
    old: { id: "old", title: "Índice anterior", status: "ready", purpose: "full", is_active: true, version: 1,
      include_public: false, profile: { engine: "fixture", model: "previous-model" }, job_id: "old-job", job_status: "completed",
      counts: { ready: 12 }, searchable: { transcription: 10, mention: 2 }, outdated: 0, error: null, research_run_id: null },
  };
  let buildBody: any = null;
  const activations: any[] = [];
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (path === "/api/search/indexes/active") return route.fulfill({ json: { id: active, model: generations[active].profile.model, include_public: false } });
    if (request.method() === "POST" && path === "/api/search/indexes") {
      buildBody = request.postDataJSON();
      generations.new = { ...generations.old, id: "new", title: buildBody.title, status: "building", is_active: false,
        job_id: "new-job", job_status: "queued", profile: { engine: "fixture", model: "new-model" }, counts: { pending: 12 }, searchable: {}, version: 1 };
      return route.fulfill({ status: 201, json: generations.new });
    }
    if (path === "/api/search/indexes") return route.fulfill({ json: Object.values(generations) });
    if (request.method() === "POST" && path.endsWith("/activate")) {
      const id = path.split("/").at(-2)!; const body = request.postDataJSON();
      expect(body.expected_active_id).toBe(active); expect(body.expected_version).toBe(generations[id].version);
      activations.push({ id, ...body });
      generations[active].is_active = false; generations[active].version += 1;
      active = id; generations[id].is_active = true; generations[id].version += 1;
      return route.fulfill({ json: generations[id] });
    }
    const id = path.split("/").at(-1)!;
    if (generations[id]) return route.fulfill({ json: generations[id] });
    throw new Error(`Petición no prevista: ${request.method()} ${path}`);
  });
  await page.goto(`${base}/e2e/indexes-harness.html`);
  const build = page.getByRole("button", { name: "Construir sin sustituir el índice activo" });
  await expect(build).toBeDisabled();
  await page.getByLabel("Título del nuevo índice").fill("Índice nuevo");
  await page.getByLabel("Autorizo construir con el perfil").check();
  await page.getByLabel("Incluir documentos públicos").check();
  await expect(build).toBeDisabled();
  await page.getByLabel("Autorizo construir con el perfil").check(); await build.click();
  await expect(page.getByRole("status")).toContainText("El índice activo no se ha sustituido");
  expect(active).toBe("old"); expect(buildBody.authorize).toBe(true); expect(buildBody.include_public).toBe(true);
  generations.new = { ...generations.new, status: "ready", job_status: "completed", counts: { ready: 12 }, searchable: { transcription: 10, mention: 2 }, version: 2 };
  await page.reload();
  await page.getByRole("button", { name: "Índice nuevo · Lista" }).click();
  const activate = page.getByRole("button", { name: "Activar conservando la anterior" });
  await expect(activate).toBeDisabled();
  await page.getByLabel("Motivo del cambio de índice").fill("Cobertura revisada en prueba sintética");
  await page.getByLabel("He revisado el modelo y la cobertura").check(); await activate.click();
  await expect(page.getByRole("status")).toContainText("la generación anterior se conserva");
  expect(active).toBe("new"); expect(generations.old.counts.ready).toBe(12);
  await page.getByRole("button", { name: "Índice anterior · Lista" }).click();
  await page.getByLabel("Motivo del cambio de índice").fill("Volver a la versión anterior verificada");
  await page.getByLabel("He revisado el modelo y la cobertura").check(); await activate.click();
  await expect(page.getByRole("status")).toContainText("Índice activado");
  expect(active).toBe("old"); expect(activations.map((a) => a.id)).toEqual(["new", "old"]);
});

test("una activación concurrente devuelve conflicto y no se presenta como éxito", async ({ page }) => {
  const generation = { id: "new", title: "Nueva", status: "ready", purpose: "full", is_active: false, version: 1,
    include_public: false, profile: { engine: "fixture", model: "test" }, counts: { ready: 1 }, searchable: { transcription: 1 }, outdated: 0 };
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "POST") return route.fulfill({ status: 409, json: { detail: "Otra persona ha cambiado el índice activo." } });
    return route.fulfill({ json: path.endsWith("/active") ? { id: "old", model: "old" } : path.endsWith("/new") ? generation : [generation] });
  });
  await page.goto(`${base}/e2e/indexes-harness.html`);
  await page.getByRole("button", { name: "Nueva · Lista" }).click();
  await page.getByLabel("Motivo del cambio de índice").fill("Probar conflicto de revisión");
  await page.getByLabel("He revisado el modelo y la cobertura").check();
  await page.getByRole("button", { name: "Activar conservando la anterior" }).click();
  await expect(page.getByRole("status")).toContainText("Otra persona ha cambiado el índice activo");
  await expect(page.getByText("Índice activo: old", { exact: true })).toBeVisible();
});
