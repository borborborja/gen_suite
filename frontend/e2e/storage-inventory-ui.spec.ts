import { test, expect } from "@playwright/test";

const base = process.env.INGEST_UI_BASE_URL;
test.skip(!base, "Solo servidor aislado y API simulada; nunca modifica almacenamiento real");

const fixture = (extra: Record<string, unknown> = {}) => ({
  id: "scan", job_id: "same-job", job_status: "error", phase: "parts", bucket_role: "private", reason: "Colección histórica",
  created_by: "fixture-admin", created_at: "2026-09-13T00:00:00Z", started_at: "2026-09-13T00:00:01Z", completed_at: null,
  invalidated_at: null, storage_configuration_matches: true, error: "Inventario incompleto: conexión interrumpida.",
  consistent_snapshot: false, quota_enforced: false, measurement: "observed_payload_bytes", observed_payload_bytes: 37,
  listing_finished: false, upload_inspection_complete: false,
  counts: { current_versions: 3, noncurrent_versions: 2, delete_markers: 1, multipart_uploads: 1, multipart_parts: 1, uninspected_uploads: 1 },
  bytes: { current_versions: 18, noncurrent_versions: 9, multipart_parts: 10 },
  groups: [{ bucket_role: "private", category: "other", payload_bytes: 37, versions: 5, delete_markers: 1, multipart_uploads: 1, multipart_parts: 1 }],
  ...extra,
});

test("el inventario distingue bytes observados de cuota y reanuda el mismo trabajo sin borrar", async ({ page }) => {
  let current = fixture(); const actions: string[] = [];
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (request.method() === "POST") {
      const action = path.split("/").at(-1)!; actions.push(action);
      expect(["retry", "cancel"]).toContain(action);
      current = fixture({ job_status: action === "retry" ? "queued" : "cancelled", error: null });
      return route.fulfill({ status: 202, json: current });
    }
    return route.fulfill({ json: path.endsWith("/scan") ? current : [current] });
  });
  await page.goto(`${base}/e2e/storage-inventory-harness.html`);
  await expect(page.getByText("No es una cuota activa", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Colección histórica · Incompleto" }).click();
  await expect(page.getByText("Recuento parcial, recorrido sin terminar", { exact: false })).toBeVisible();
  await expect(page.getByText("Su tamaño no se presume cero", { exact: false })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Otros objetos", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Reanudar el mismo inventario" }).click();
  await expect(page.getByRole("status")).toContainText("no modifica objetos ni cuotas");
  expect(current.job_id).toBe("same-job");
  await page.getByRole("button", { name: "Cancelar solo el inventario" }).click();
  await expect(page.getByRole("status")).toContainText("no se borra ningún objeto");
  await expect(page.getByRole("button", { name: "Reanudar el mismo inventario" })).toHaveCount(0);
  expect(actions).toEqual(["retry", "cancel"]);
});

test("tras restaurar exige otro inventario y un fallo de red reutiliza la identidad de creación", async ({ page }) => {
  const historical = fixture({ job_status: "paused", invalidated_at: "2026-09-13T01:00:00Z", storage_configuration_matches: false });
  let current = historical; const requests: any[] = [];
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    const request = route.request(); const path = new URL(request.url()).pathname;
    if (request.method() === "POST") {
      expect(path).toBe("/api/storage/inventories"); requests.push(request.postDataJSON());
      if (requests.length === 1) return route.abort("failed");
      current = fixture({ id: "new", reason: requests[1].reason, job_status: "queued", error: null });
      return route.fulfill({ status: 202, json: current });
    }
    return route.fulfill({ json: /\/(scan|new)$/.test(path) ? current : [historical] });
  });
  await page.goto(`${base}/e2e/storage-inventory-harness.html`);
  await page.getByRole("button", { name: "Colección histórica · Pausado" }).click();
  await expect(page.getByText("Inventario histórico de otro destino", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reanudar el mismo inventario" })).toHaveCount(0);
  const create = page.getByRole("button", { name: "Inventariar sin modificar archivos" });
  await expect(create).toBeDisabled();
  await page.getByLabel("Motivo del inventario").fill("Medir el destino restaurado");
  await create.click();
  await expect(page.getByRole("status")).not.toBeEmpty();
  await expect(create).toBeEnabled(); await create.click();
  await expect(page.getByRole("status")).toContainText("Inventario encolado");
  expect(requests).toHaveLength(2); expect(requests[0]).toEqual(requests[1]);
  expect(requests[0]).not.toHaveProperty("bucket"); expect(requests[0]).not.toHaveProperty("prefix");
});
