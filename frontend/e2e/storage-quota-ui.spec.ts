import { test, expect } from "@playwright/test";
const base = process.env.INGEST_UI_BASE_URL;
test.skip(!base, "API simulada y servidor aislado; sin S3 ni base de datos reales");

const fixture = () => ({ phase: "ready", used_bytes: 100, reserved_bytes: 20, limit_bytes: 200,
  pending_writes: 1, location_matches: true, baseline_at: "2026-09-13T00:00:00Z", quota_enforced: true,
  measurement: "conservative_payload_bytes", scan: null as any });

test("recontar exige confirmación y cancelar no inventa espacio liberado", async ({ page }) => {
  const state = fixture(); const actions: any[] = [];
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    const req = route.request(); const path = new URL(req.url()).pathname;
    if (path.endsWith("/writes")) return route.fulfill({ json: [] });
    if (req.method() === "POST") {
      if (path.endsWith("/recount")) {
        actions.push(req.postDataJSON()); state.phase = "draining";
        state.scan = { job_status: "error", error: "Hay una escritura previa pendiente." };
      } else if (path.endsWith("/cancel")) {
        state.phase = "ready"; state.scan.job_status = "cancelled";
      } else throw new Error(`Acción no prevista: ${path}`);
    }
    return route.fulfill({ json: state });
  });
  await page.goto(`${base}/e2e/storage-quota-harness.html`);
  const start = page.getByRole("button", { name: "Recontar y actualizar la cuota" });
  await expect(start).toBeDisabled();
  await page.getByLabel("Motivo del recuento protegido").fill("Verificar colección");
  await expect(start).toBeDisabled();
  await page.getByLabel("Acepto bloquear nuevas escrituras").check();
  await page.getByLabel("Motivo del recuento protegido").fill("Verificar colección antigua");
  await expect(start).toBeDisabled();
  await page.getByLabel("Acepto bloquear nuevas escrituras").check(); await start.click();
  await expect(page.getByText("Nuevas cargas y llamadas de IA de pago bloqueadas", { exact: false })).toBeVisible();
  expect(actions).toHaveLength(1); expect(actions[0].authorize_write_pause).toBe(true); expect(actions[0].external_writers_stopped).toBe(true);
  expect(actions[0]).not.toHaveProperty("bucket"); expect(actions[0]).not.toHaveProperty("scan_id");
  await page.getByRole("button", { name: "Cancelar recuento conservando la cuota" }).click();
  await expect(page.getByRole("status")).toContainText("sin liberar espacio estimado");
  await expect(page.getByText("Consumo conservador: 100 bytes", { exact: false })).toBeVisible();
  expect(state.used_bytes).toBe(100); expect(state.reserved_bytes).toBe(20);
});

test("una comprobación incierta conserva la reserva y nunca reenvía el objeto", async ({ page }) => {
  const state = fixture(); const calls: string[] = [];
  const pending = { id: "receipt", kind: "put", state: "unknown", reserved_bytes: 20, actual_bytes: null,
    job_id: "job", created_at: "2026-09-13T00:00:00Z", error: "Resultado S3 incierto" };
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    const req = route.request(); const path = new URL(req.url()).pathname;
    if (req.method() === "POST") {
      calls.push(path); expect(path).toBe("/api/storage/quota/writes/receipt/recheck");
      return route.fulfill({ status: 409, json: { detail: "S3 no acredita el resultado exacto. Se conserva la reserva." } });
    }
    return route.fulfill({ json: path.endsWith("/writes") ? [pending] : state });
  });
  await page.goto(`${base}/e2e/storage-quota-harness.html`);
  await page.getByText("Reservas y resultados pendientes", { exact: true }).click();
  await page.getByRole("button", { name: "Verificar recibo sin repetir escritura" }).click();
  await expect(page.getByRole("status")).toContainText("Se conserva la reserva");
  await expect(page.getByText("Reservado: 20 bytes", { exact: false })).toBeVisible();
  expect(calls).toHaveLength(1);
});

test("permite revisar recibos más allá de los primeros veinte y volver tras liquidarlos", async ({ page }) => {
  const state = fixture(); state.pending_writes = 21; state.reserved_bytes = 21;
  const rows = Array.from({ length: 21 }, (_, i) => ({ id: `receipt-${i + 1}`, kind: "put", state: i === 20 ? "unknown" : "reserved",
    reserved_bytes: 1, actual_bytes: null, job_id: null, created_at: "2026-09-13T00:00:00Z", error: null }));
  const actions: string[] = [];
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    const req = route.request(); const url = new URL(req.url());
    if (req.method() === "POST") {
      actions.push(url.pathname); expect(url.pathname).toBe("/api/storage/quota/writes/receipt-21/recheck");
      rows.pop(); state.pending_writes = 20; state.reserved_bytes = 20; state.used_bytes = 101;
      return route.fulfill({ json: { id: "receipt-21", state: "succeeded", actual_bytes: 1 } });
    }
    const offset = Number(url.searchParams.get("offset") ?? "0");
    return route.fulfill({ json: url.pathname.endsWith("/writes") ? rows.slice(offset, offset + 20) : state });
  });
  await page.goto(`${base}/e2e/storage-quota-harness.html`);
  await page.getByText("Reservas y resultados pendientes", { exact: true }).click();
  await expect(page.getByText("Recibo receipt-21 ·", { exact: false })).toHaveCount(0);
  await page.getByRole("button", { name: "Siguientes recibos" }).click();
  await expect(page.getByText("Recibo receipt-21 · Resultado incierto", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Siguientes recibos" })).toBeDisabled();
  await page.getByRole("button", { name: "Verificar recibo sin repetir escritura" }).click();
  await expect(page.getByText("Esta página ya no contiene operaciones pendientes.", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Primeros recibos" }).click();
  await expect(page.getByText("Recibo receipt-1 · Reservado, sin iniciar", { exact: false })).toBeVisible();
  expect(actions).toHaveLength(1);
});

for (const stale of [false, true]) test(`cancelar reserva exige revisión y confirmación; conflicto=${stale}`, async ({ page }) => {
  const state = fixture(); const commands: any[] = []; const audit: any[] = []; let finished = false;
  const receipt = { id: "unused", state: "reserved", kind: "put", reserved_bytes: 20, job_id: null };
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    const req = route.request(); const path = new URL(req.url()).pathname;
    if (path.endsWith("/cancel-preview")) {
      const body = req.postDataJSON(); return route.fulfill({ json: { write_id: receipt.id, state: "reserved", kind: "put", reserved_bytes: 20,
        job_id: null, ai_call_id: null, ai_state: null, requires_late_response_loss_ack: false, reason: body.reason,
        preview_hash: "a".repeat(64), storage_changed: false, job_cancelled: false } });
    }
    if (path.endsWith("/cancel-unused")) {
      const body = req.postDataJSON(); commands.push(body);
      if (stale) return route.fulfill({ status: 409, json: { detail: "La escritura ya ha empezado; su reserva no se libera." } });
      finished = true; state.reserved_bytes = 0; state.pending_writes = 0;
      const action = { id: "audit", write_id: "unused", created_by: "owner", created_at: "2026-09-13T00:00:00Z",
        reason: body.reason, released_bytes: 20, evidence: { after_state: "cancelled", late_response_loss_ack: false } };
      audit.push(action); return route.fulfill({ json: action });
    }
    if (req.method() !== "GET") throw new Error(`Escritura inesperada ${path}`);
    return route.fulfill({ json: path.endsWith("/writes") ? finished ? [] : [receipt] : path.endsWith("/actions") ? audit : state });
  });
  await page.goto(`${base}/e2e/storage-quota-harness.html`);
  await page.getByText("Reservas y resultados pendientes", { exact: true }).click();
  await page.getByRole("button", { name: "Revisar cancelación de reserva no iniciada" }).click();
  const review = page.getByRole("region", { name: "Revisar cancelación de reserva", exact: true });
  await review.getByLabel("Motivo de cancelación").fill("Carga sintética abandonada");
  await review.getByRole("button", { name: "Revisar efectos de cancelación" }).click();
  await expect(review.getByRole("button", { name: "Confirmar cancelación de reserva" })).toBeDisabled();
  await review.getByLabel("Confirmo cancelar únicamente esta reserva", { exact: false }).check();
  await review.getByLabel("Motivo de cancelación").fill("Carga sintética revisada y abandonada");
  await expect(review.getByRole("button", { name: "Confirmar cancelación de reserva" })).toHaveCount(0);
  await review.getByRole("button", { name: "Revisar efectos de cancelación" }).click();
  await review.getByLabel("Confirmo cancelar únicamente esta reserva", { exact: false }).check();
  await review.getByRole("button", { name: "Confirmar cancelación de reserva" }).click();
  expect(commands).toHaveLength(1); expect(commands[0].authorize).toBe(true);
  expect(commands[0].acknowledge_late_response_loss).toBe(false); expect(commands[0]).not.toHaveProperty("bucket");
  if (stale) {
    await expect(review.getByRole("status")).toContainText("su reserva no se libera");
    expect(state.reserved_bytes).toBe(20); expect(audit).toHaveLength(0);
  } else {
    await expect(review).toHaveCount(0); await expect(page.getByRole("status")).toContainText("No se han borrado objetos");
    await page.getByText("Auditoría de reservas canceladas", { exact: true }).click();
    await expect(page.getByText("Recibo unused · Carga sintética revisada", { exact: false })).toBeVisible();
    expect(state.used_bytes).toBe(100); expect(audit).toHaveLength(1);
  }
});

test("liberar una reserva de IA conciliada exige aceptar por separado la pérdida de respuesta tardía", async ({ page }) => {
  const state = fixture(); let attempts = 0; const bodies: any[] = [];
  await page.route((url) => url.pathname.startsWith("/api/"), (route) => {
    const req = route.request(); const path = new URL(req.url()).pathname;
    if (path.endsWith("/cancel-preview")) return route.fulfill({ json: { write_id: "ai-unused", state: "reserved", reserved_bytes: 20,
      ai_call_id: "paid-call", ai_state: "reconciled", requires_late_response_loss_ack: true,
      reason: req.postDataJSON().reason, preview_hash: "b".repeat(64), storage_changed: false, job_cancelled: false } });
    if (path.endsWith("/cancel-unused")) {
      bodies.push(req.postDataJSON()); attempts += 1;
      if (attempts === 1) return route.abort("failed");
      state.reserved_bytes = 0; state.pending_writes = 0;
      return route.fulfill({ json: { id: "audit-paid", write_id: "ai-unused", released_bytes: 20 } });
    }
    if (req.method() !== "GET") throw new Error(`No se debe reenviar IA ni escribir S3: ${path}`);
    return route.fulfill({ json: path.endsWith("/writes") ? state.pending_writes ? [{ id: "ai-unused", kind: "put", state: "reserved", reserved_bytes: 20 }] : [] : state });
  });
  await page.goto(`${base}/e2e/storage-quota-harness.html`);
  await page.getByText("Reservas y resultados pendientes", { exact: true }).click();
  await page.getByRole("button", { name: "Revisar cancelación de reserva no iniciada" }).click();
  const review = page.getByRole("region", { name: "Revisar cancelación de reserva", exact: true });
  await review.getByLabel("Motivo de cancelación").fill("Llamada conciliada y resultado descartado");
  await review.getByRole("button", { name: "Revisar efectos de cancelación" }).click();
  await expect(review.getByRole("alert")).toContainText("Su coste no cambia");
  await review.getByLabel("Confirmo cancelar únicamente esta reserva", { exact: false }).check();
  const submit = review.getByRole("button", { name: "Confirmar cancelación de reserva" });
  await expect(submit).toBeDisabled();
  await review.getByLabel("Renuncio a guardar una respuesta tardía", { exact: false }).check(); await submit.click();
  await expect(review.getByRole("status")).not.toBeEmpty(); await submit.click();
  await expect(review).toHaveCount(0);
  expect(bodies).toHaveLength(2); expect(bodies[0]).toEqual(bodies[1]); expect(bodies[1].acknowledge_late_response_loss).toBe(true);
});
