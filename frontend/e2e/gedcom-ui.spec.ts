import { test, expect, type Page } from "@playwright/test";

const base = process.env.INGEST_UI_BASE_URL;
test.skip(!base, "Solo Vite aislado y respuestas sintéticas; sin instalación personal");
const sha = "a".repeat(64);
const report = {
  format: "GEDCOM 5.5.1", scope: "genealogy_interchange", is_backup: false, sha256: sha,
  review_token: "b".repeat(64),
  counts: { persons: 2, families: 1, sources: 2, repositories: 1, citations: 5, citations_exported: 4 },
  extensions: ["_GS_SOURCE", "_GS_CITATION"], excluded_records: { documents: 3, research_assertions: 1 },
  unattached_citations: 1, relocated_child_citations: 1, raw_structures: 1, unresolved_raw_references: 1,
  warnings: ["No incluye imágenes. No es una copia de seguridad.", "Incluye personas privadas o vivas."],
};

async function importPanel(page: Page, mismatch = false) {
  await intercept(page);
  let item: any = null;
  const requests: { method: string; path: string; body?: any }[] = [];
  const audit = { format: "GEDCOM 7.0", input_sha256: mismatch ? "f".repeat(64) : sha, input_bytes: 3,
    review_token: "c".repeat(64), counts: { INDI: 2, FAM: 1, SOUR: 1, citations: 2, citations_modeled: 1 },
    media: { documents: 1, files: 2, remote_not_fetched: 1, local_not_attached: 0 },
    warnings: ["Una cita se conserva solo en el original. No se fusionan árboles."],
    change_counts: { citation_retained_only_in_original: 1 }, changes: [], changes_truncated: false };
  await page.route("**/api/tree/imports**", (route) => {
    const request = route.request(); const url = new URL(request.url());
    requests.push({ method: request.method(), path: url.pathname,
      body: url.pathname.endsWith("/apply") ? request.postDataJSON() : undefined });
    if (url.pathname.endsWith("/audit")) return route.fulfill({ json: audit });
    if (url.pathname.endsWith("/apply")) {
      item = { ...item, apply_job_id: "apply-1", apply_status: "queued" };
      return route.fulfill({ status: 202, json: item });
    }
    if (request.method() === "POST") {
      expect(url.searchParams.get("kind")).toBe("gedzip");
      expect(url.searchParams.get("client_id")).toBeTruthy();
      item = { id: "import-1", client_id: "client-1", filename: "book.gdz", kind: "gedzip", byte_size: 3, checksum: sha,
        status: "queued", error: null, review_ready: false, apply_status: null, apply_job_id: null, created_at: "2026-09-13T12:00:00Z", result: null };
      return route.fulfill({ status: 202, json: item });
    }
    return route.fulfill({ json: item ? [item] : [] });
  });
  await page.route("**/api/jobs/apply-1/cancel", (route) => {
    item = { ...item, apply_status: "cancelled" };
    return route.fulfill({ json: { status: "cancelled" } });
  });
  await page.getByRole("button", { name: "Importación revisada GEDCOM 7 y GEDZIP" }).click();
  const panel = page.getByRole("region", { name: "Importaciones revisadas del árbol" });
  await panel.getByLabel("Archivo GEDCOM 7 o GEDZIP").setInputFiles({ name: "book.gdz", mimeType: "application/zip", buffer: Buffer.from("zip") });
  await panel.getByRole("button", { name: "Cargar y analizar, sin incorporar" }).click();
  await expect(panel.getByText(/book.gdz · Análisis: En cola/)).toBeVisible();
  item = { ...item, status: "completed", review_ready: true };
  await panel.getByRole("button", { name: "Actualizar importaciones" }).click();
  return { panel, requests };
}

test("importación exige informe y aceptación; reabrir no duplica y cancelar exige reintento", async ({ page }) => {
  const { panel, requests } = await importPanel(page);
  await panel.getByRole("button", { name: "Revisar importación" }).click();
  await expect(panel.getByText(/Una cita se conserva solo en el original/)).toBeVisible();
  await expect(panel.getByRole("button", { name: "Incorporar árbol revisado" })).toBeDisabled();
  expect(requests.filter((r) => r.path.endsWith("/apply"))).toEqual([]);
  await panel.getByRole("checkbox").check();
  await panel.getByRole("button", { name: "Incorporar árbol revisado" }).click();
  expect(requests.find((r) => r.path.endsWith("/apply"))?.body).toEqual({ review_token: "c".repeat(64), acknowledge_append: true, retry_failed: false });
  await page.getByRole("button", { name: "Importación revisada GEDCOM 7 y GEDZIP" }).click();
  await page.getByRole("button", { name: "Importación revisada GEDCOM 7 y GEDZIP" }).click();
  await expect(panel.getByText(/Incorporación: En cola/)).toBeVisible();
  expect(requests.filter((r) => r.path.endsWith("/apply"))).toHaveLength(1);
  await panel.getByRole("button", { name: "Cancelar tarea de importación" }).click();
  await panel.getByRole("button", { name: "Revisar importación" }).click();
  await expect(panel.getByRole("button", { name: "Reintentar incorporación revisada" })).toBeDisabled();
  await panel.getByRole("checkbox").check();
  await panel.getByRole("button", { name: "Reintentar incorporación revisada" }).click();
  expect(requests.filter((r) => r.path.endsWith("/apply"))[1].body.retry_failed).toBe(true);
});

test("informe de otro original impide incorporar", async ({ page }) => {
  const { panel, requests } = await importPanel(page, true);
  await panel.getByRole("button", { name: "Revisar importación" }).click();
  await expect(panel.getByRole("alert")).toContainText("El informe no corresponde al original");
  expect(requests.filter((r) => r.path.endsWith("/apply"))).toEqual([]);
  await expect(panel.getByRole("button", { name: "Incorporar árbol revisado" })).toHaveCount(0);
});

async function intercept(page: Page, changed = false) {
  const requests: string[] = [];
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    const url = new URL(route.request().url());
    requests.push(url.pathname + url.search);
    if (url.pathname.endsWith("/export/gedcom/audit")) return route.fulfill({ json: report });
    if (url.pathname.endsWith("/export/gedcom")) return route.fulfill(changed
      ? { status: 409, json: { detail: "El árbol cambió después de revisar el informe." } }
      : { contentType: "text/plain", body: "0 HEAD\n1 GEDC\n2 VERS 5.5.1\n0 TRLR\n" });
    if (url.pathname.endsWith("/tree/stats")) return route.fulfill({ json: { persons: 2, families: 1, events: 1, places: 0 } });
    if (url.pathname.endsWith("/tree/home")) return route.fulfill({ json: { person_id: null } });
    return route.fulfill({ json: [] });
  });
  await page.goto(`${base}/e2e/gedcom-harness.html`);
  return requests;
}

test("revisa pérdidas y privacidad sin descargar ni escribir al cancelar", async ({ page }) => {
  const requests = await intercept(page);
  await page.getByRole("button", { name: "⬇ Exportar GEDCOM" }).click();
  await expect(page.getByText("Citas exportadas: 4 de 5.")).toBeVisible();
  await expect(page.getByText("No incluye imágenes. No es una copia de seguridad.")).toBeVisible();
  await expect(page.getByText("Incluye personas privadas o vivas.")).toBeVisible();
  await expect(page.getByText(/1 citas no tienen un destino representable/)).toBeVisible();
  await page.getByRole("button", { name: "Cancelar", exact: true }).click();
  expect(requests.filter((url) => url.includes("/export/gedcom?"))).toEqual([]);
  await expect(page.getByRole("button", { name: "⬇ Exportar GEDCOM" })).toBeEnabled();
});

test("informe descargable y GEDCOM quedan ligados al SHA revisado", async ({ page }) => {
  const requests = await intercept(page);
  await page.getByRole("button", { name: "⬇ Exportar GEDCOM" }).click();
  const auditDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Guardar informe de pérdidas (JSON)" }).click();
  expect((await auditDownload).suggestedFilename()).toBe("gen_suite_gedcom_audit.json");
  const treeDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Descargar GEDCOM privado" }).click();
  expect((await treeDownload).suggestedFilename()).toBe("gen_suite_export.ged");
  expect(requests).toContain(`/api/tree/export/gedcom?expected_sha256=${sha}&expected_review_token=${report.review_token}`);
});

test("rechaza el archivo cambiado y permite revisar de nuevo", async ({ page }) => {
  await intercept(page, true);
  let downloads = 0;
  page.on("download", () => downloads++);
  await page.getByRole("button", { name: "⬇ Exportar GEDCOM" }).click();
  await page.getByRole("button", { name: "Descargar GEDCOM privado" }).click();
  await expect(page.getByRole("status")).toContainText("El árbol cambió");
  expect(downloads).toBe(0);
  await expect(page.getByRole("button", { name: "⬇ Exportar GEDCOM" })).toBeEnabled();
});

async function asyncExports(page: Page, bytes = 32) {
  await page.addInitScript(() => Object.defineProperty(window, "showSaveFilePicker", { value: undefined, configurable: true }));
  let item: Record<string, unknown> | null = null;
  const requests: string[] = [];
  const output = "0 HEAD\n1 GEDC\n2 VERS 7.0\n0 TRLR\n";
  const audit = { ...report, format: "GEDCOM 7.0", artifact_sha256: sha, artifact_byte_size: bytes,
    snapshot: { isolation: "repeatable_read", captured_at: "2026-09-13 12:00:00+00:00" },
    media_documents: 2, media_files: 3 };
  await intercept(page);
  // init script takes effect on this fresh navigation.
  await page.route("**/api/tree/exports**", (route) => {
    const url = new URL(route.request().url());
    requests.push(`${route.request().method()} ${url.pathname}${url.search}`);
    if (url.pathname.endsWith("/audit")) return route.fulfill({ json: audit });
    if (url.pathname.endsWith("/content")) return route.fulfill({ body: output, contentType: "application/octet-stream" });
    if (route.request().method() === "POST") {
      item = { id: "export-1", kind: route.request().postDataJSON().kind, status: "queued", created_at: "2026-09-13T12:00:00Z", progress: null, error: null, checksum: sha, byte_size: bytes };
      return route.fulfill({ status: 202, json: item });
    }
    return route.fulfill({ json: item ? [item] : [] });
  });
  await page.route("**/api/jobs/export-1/cancel", (route) => {
    if (item) item.status = "cancelled";
    requests.push("cancel");
    return route.fulfill({ json: { status: "cancelled" } });
  });
  await page.reload();
  await page.getByRole("button", { name: "Exportaciones en segundo plano y GEDZIP" }).click();
  return { requests, complete: () => { if (item) item.status = "completed"; } };
}

test("exportación en segundo plano recuperable al reabrir y descarga ligada al informe", async ({ page }) => {
  const { requests, complete } = await asyncExports(page);
  const panel = page.getByRole("region", { name: "Exportaciones privadas del árbol" });
  await expect(panel.getByText(/no es una publicación ni una copia de seguridad completa/)).toBeVisible();
  await panel.getByRole("button", { name: "Generar exportación privada" }).click();
  await expect(panel.getByRole("button", { name: "Cancelar exportación" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Generar exportación privada" })).toBeDisabled();
  await page.getByRole("button", { name: "Exportaciones en segundo plano y GEDZIP" }).click();
  complete();
  await page.getByRole("button", { name: "Exportaciones en segundo plano y GEDZIP" }).click();
  await panel.getByRole("button", { name: "Revisar informe" }).click();
  await expect(panel.getByText("Documentos incluidos: 2 · Archivos originales: 3.")).toBeVisible();
  await expect(panel.getByText(/Los cambios posteriores del árbol no cambian este archivo/)).toBeVisible();
  expect(requests.some((value) => value.includes("/content"))).toBe(false);
  const downloaded = page.waitForEvent("download");
  await panel.getByRole("button", { name: "Descargar archivo privado revisado" }).click();
  expect((await downloaded).suggestedFilename()).toBe("export-1.ged");
  expect(requests).toContain(`GET /api/tree/exports/export-1/content?review_token=${report.review_token}`);
});

test("GEDZIP permite cancelar sin mostrar descarga ni informe de éxito", async ({ page }) => {
  const { requests } = await asyncExports(page);
  const panel = page.getByRole("region", { name: "Exportaciones privadas del árbol" });
  await panel.getByLabel("Formato de exportación").selectOption("gedzip");
  await panel.getByRole("button", { name: "Generar exportación privada" }).click();
  await panel.getByRole("button", { name: "Cancelar exportación" }).click();
  await expect(panel.getByText(/GEDZIP con documentos · Cancelada/)).toBeVisible();
  await expect(panel.getByRole("button", { name: "Revisar informe" })).toHaveCount(0);
  await expect(panel.getByRole("button", { name: "Generar exportación privada" })).toBeEnabled();
  expect(requests).toContain("cancel");
  expect(requests.some((value) => value.includes("/content"))).toBe(false);
});

test("paquete grande exige guardado a disco sin descargarlo entero en memoria", async ({ page }) => {
  const { requests, complete } = await asyncExports(page, 300 * 1024**2);
  const panel = page.getByRole("region", { name: "Exportaciones privadas del árbol" });
  await panel.getByRole("button", { name: "Generar exportación privada" }).click();
  await expect(panel.getByRole("button", { name: "Cancelar exportación" })).toBeVisible();
  complete();
  await panel.getByRole("button", { name: "Actualizar exportaciones" }).click();
  await panel.getByRole("button", { name: "Revisar informe" }).click();
  await panel.getByRole("button", { name: "Descargar archivo privado revisado" }).click();
  await expect(panel.getByRole("alert")).toContainText("supera 200 MB");
  expect(requests.some((value) => value.includes("/content"))).toBe(false);
});
