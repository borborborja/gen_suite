import { test, expect } from "@playwright/test";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const base = process.env.INGEST_UI_BASE_URL;
test.skip(!base, "Requiere servidor Vite de pruebas local, no una instalación con datos");

test("reanudar la misma colección después de un error y una recarga no reenvía el primer original", async ({ page }, testInfo) => {
  if (!base || !["127.0.0.1", "localhost", "::1"].includes(new URL(base).hostname)) throw new Error("Solo servidor de pruebas local");
  const folder = testInfo.outputPath("originals");
  await mkdir(folder, { recursive: true });
  const paths = [path.join(folder, "1.jpg"), path.join(folder, "2.jpg")];
  await writeFile(paths[0], Buffer.from("first-original"));
  await writeFile(paths[1], Buffer.from("second-original"));
  let session: any = null;
  const files: any[] = [];
  const books: any[] = [];
  const sent: Record<string, number> = {};
  let failSecond = true;
  // Every API request is intercepted. This test never contacts a real backend or provider.
  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const routePath = url.pathname.replace("/api/documents/ingestion", "");
    const reply = (json: unknown, status = 200) => route.fulfill({ status, json });
    if (url.pathname.indexOf("/api/documents/ingestion") !== 0) return reply({ detail: "API fuera de alcance" }, 404);
    if (!routePath && req.method() === "GET") return reply(session ? [session] : []);
    if (!routePath && req.method() === "POST") {
      const body = req.postDataJSON();
      session = { ...body, id: "11111111-1111-4111-8111-111111111111", status: "manifest", declared_files: 0,
        declared_bytes: 0, received_bytes: 0, chunk_bytes: 8 * 1024 ** 2, created_at: new Date().toISOString(), job_id: null };
      return reply(session, 201);
    }
    if (routePath === `/${session?.id}`) return reply(session);
    if (routePath.endsWith("/manifest")) {
      const body = req.postDataJSON();
      for (const entry of body.files) {
        if (!books.some((book) => book.group === entry.group)) books.push({ id: "book", ...entry, status: "pending", document_id: null });
        if (!files.some((file) => file.path === entry.path)) files.push({ ...entry, id: `file-${entry.position}`, book_id: "book",
          status: "pending", received_bytes: 0, parts: {}, checksum: null, error: null });
      }
      session.declared_files = files.length; session.declared_bytes = files.reduce((sum, item) => sum + item.byte_size, 0);
      return reply(files);
    }
    if (routePath.endsWith("/seal")) { session.status = "uploading"; return reply(session); }
    if (routePath.endsWith("/books")) return reply(books);
    if (routePath.endsWith("/complete")) {
      session.status = "processing"; session.job_id = "job";
      return reply(session, 202);
    }
    const match = routePath.match(/\/files\/([^/]+)(?:\/chunks\/(\d+))?$/);
    if (match) {
      const file = files.find((item) => item.id === match[1]);
      if (req.method() === "GET") return reply(file);
      sent[file.id] = (sent[file.id] || 0) + 1;
      if (file.position === 2 && failSecond) { failSecond = false; return reply({ detail: "Fallo de red simulado" }, 503); }
      const content = req.postDataBuffer()!;
      const digest = createHash("sha256").update(content).digest("hex");
      expect(req.headers()["x-content-sha256"]).toBe(digest);
      file.parts[match[2]] = { size: content.length, sha256: digest };
      file.received_bytes += content.length; session.received_bytes += content.length;
      return reply(file);
    }
    return reply({ detail: `Ruta simulada no definida: ${routePath}` }, 404);
  });
  await page.goto(`${base}/e2e/ingestion-harness.html`);
  await page.locator('input[type="file"]').nth(1).setInputFiles(paths);
  await page.getByRole("button", { name: "Guardar manifiesto y enviar" }).click();
  await expect(page.getByRole("alert")).toContainText("Fallo de red simulado");
  expect(sent["file-1"]).toBe(1);
  await page.reload();
  await page.getByLabel("Sesión guardada").selectOption("11111111-1111-4111-8111-111111111111");
  await expect(page.getByRole("button", { name: "Reanudar envío" })).toBeVisible();
  await page.locator('input[type="file"]').nth(1).setInputFiles(paths);
  await page.getByRole("button", { name: "Reanudar envío" }).click();
  await expect(page.getByRole("status")).toContainText("Originales recibidos");
  expect(sent["file-1"]).toBe(1);
  expect(sent["file-2"]).toBe(2);
  expect(session.received_bytes).toBe(session.reserved_bytes);
});
