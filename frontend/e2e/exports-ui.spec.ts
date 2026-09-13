import { test, expect } from "@playwright/test";

const base = process.env.INGEST_UI_BASE_URL;
test.skip(!base, "Solo Vite aislado y respuestas simuladas; sin documentos personales");

test("PDF/A ausente no impide PDF normal ni BagIt", async ({ page }) => {
  const writes: string[] = [];
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "POST") writes.push(path);
    return route.fulfill({ json: path.endsWith("export-capabilities") ? { pdfa_available: false } : [] });
  });
  await page.goto(`${base}/e2e/exports-harness.html`);
  await expect(page.getByText(/PDF\/A no disponible:/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Crear PDF/A-2b validado" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Crear PDF con transcripción" })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Crear paquete de preservación" })).toBeEnabled();
  expect(writes).toEqual([]);
});

test("worker caído después del heartbeat muestra error, sin éxito ficticio", async ({ page }) => {
  const bodies: unknown[] = [];
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "POST") {
      bodies.push(route.request().postDataJSON());
      return route.fulfill({ status: 503, json: { detail: "El worker PDF/A no está disponible." } });
    }
    return route.fulfill({ json: path.endsWith("export-capabilities") ? { pdfa_available: true } : [] });
  });
  await page.goto(`${base}/e2e/exports-harness.html`);
  await page.getByRole("button", { name: "Crear PDF/A-2b validado" }).click();
  await expect(page.getByRole("alert")).toContainText("El worker PDF/A no está disponible");
  expect(bodies).toEqual([{ kind: "pdfa" }]);
  await expect(page.getByText(/Generando en segundo plano/)).toHaveCount(0);
});

test("derivado obsoleto conserva informe descargable y no certifica exactitud", async ({ page }) => {
  const report = { export_id: "export", checksum: "abc", validation: { status: "validated" } };
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({ json: path.endsWith("validation") ? report : path.endsWith("export-capabilities") ? { pdfa_available: true } : [{
      id: "export", kind: "pdfa", byte_size: 100, checksum: "abc", stale: true, pdfa: "validated",
      created_at: "2026-09-13T00:00:00Z", text_mode: "appendix", scope: "document",
    }] });
  });
  await page.goto(`${base}/e2e/exports-harness.html`);
  await expect(page.getByText(/PDF\/A-2b validado por veraPDF/)).toBeVisible();
  await expect(page.getByText(/Hay transcripciones posteriores/)).toBeVisible();
  await expect(page.getByText(/no certifica la exactitud de la transcripción/)).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Descargar informe de validación" }).click();
  expect((await download).suggestedFilename()).toBe("export-validation.json");
});
