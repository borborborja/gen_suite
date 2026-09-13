import { test, expect } from "@playwright/test";

const base = process.env.INGEST_UI_BASE_URL;
test.skip(!base, "API simulada en servidor aislado; nunca elimina objetos reales");
for (const stale of [false, true]) {
  test(stale ? "una sesión modificada exige volver a confirmar la limpieza" : "limpiar requiere confirmaciones separadas y cierra la reanudación", async ({ page }) => {
    const sid = "11111111-1111-4111-8111-111111111111";
    let approved: any = null; let cleanup: any = null;
    const session = { id: sid, title: "Carga abandonada", status: "uploading", expected_files: 2, declared_files: 2,
      reserved_bytes: 100, declared_bytes: 100, received_bytes: 50, options: {}, job_id: null, chunk_bytes: 8388608 };
    await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
      const request = route.request(); const path = new URL(request.url()).pathname;
      if (path.endsWith("/cleanup/preview")) return route.fulfill({ json: { session_id: sid, title: session.title, status: "uploading",
        files: 2, published_books: 0, mode: "discard", received_bytes: 50, reserved_bytes: 100,
        retained_reservation_bytes: 0, released_reservation_bytes: 100, preview_hash: "a".repeat(64) } });
      if (path.endsWith("/cleanup") && request.method() === "POST") {
        if (stale) return route.fulfill({ status: 409, json: { detail: "La sesión ha cambiado. Revisa la comparación antes de eliminar temporales." } });
        approved = request.postDataJSON(); session.status = "cleaning";
        cleanup = { id: "cleanup", session_id: sid, job_id: "job", mode: "discard", phase: "inventory", inventory_epoch: 1,
          job_status: "queued", planned_objects: 0, planned_bytes: 0, deleted_objects: 0, deleted_bytes: 0, reason: approved.reason,
          created_by: "test-admin", retained_reservation_bytes: 0 };
        return route.fulfill({ status: 202, json: cleanup });
      }
      if (path.endsWith("/cleanup")) return route.fulfill({ json: cleanup });
      if (path.endsWith("/books")) return route.fulfill({ json: [] });
      if (path.endsWith(sid)) return route.fulfill({ json: session });
      return route.fulfill({ json: [session] });
    });
    await page.goto(`${base}/e2e/ingestion-harness.html`);
    await page.getByLabel("Sesión guardada").selectOption(sid);
    await page.getByText("Liberar temporales de esta sesión (administración)", { exact: true }).click();
    await page.getByLabel("Motivo de la limpieza").fill("Originales locales verificados, descarto esta carga");
    await page.getByRole("button", { name: "Comparar limpieza antes de autorizar" }).click();
    const confirm = page.getByRole("button", { name: "Cerrar sesión y eliminar temporales" });
    await expect(confirm).toBeDisabled();
    await page.getByLabel("Conservo y he comprobado").check(); await expect(confirm).toBeDisabled();
    await page.getByLabel("Autorizo eliminar permanentemente solo").check();
    await page.getByLabel("Motivo de la limpieza").fill("Revisión modificada: copia local comprobada");
    await expect(confirm).toHaveCount(0);
    await page.getByRole("button", { name: "Comparar limpieza antes de autorizar" }).click();
    await expect(page.getByLabel("Conservo y he comprobado")).not.toBeChecked();
    await page.getByLabel("Conservo y he comprobado").check();
    await page.getByLabel("Autorizo eliminar permanentemente solo").check();
    await confirm.click();
    if (stale) {
      await expect(page.getByRole("status")).toContainText("ha cambiado");
      await expect(confirm).toHaveCount(0); expect(approved).toBeNull();
      await expect(page.getByRole("button", { name: "Reanudar envío" })).toBeVisible();
    } else {
      expect(approved.authorize_permanent_staging_deletion).toBe(true); expect(approved.local_originals_confirmed).toBe(true);
      expect(approved).not.toHaveProperty("prefix"); expect(approved).not.toHaveProperty("bucket");
      await expect(page.getByRole("status")).toContainText("Sesión cerrada para limpieza");
      await expect(page.getByRole("button", { name: "Reanudar envío" })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Verificar / reintentar libros sin volver a subir" })).toHaveCount(0);
    }
  });
}
