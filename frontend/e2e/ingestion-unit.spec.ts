import { test, expect } from "@playwright/test";
import { File as NodeFile } from "node:buffer";
import { createHash } from "node:crypto";
import { naturalPathOrder, planCollection, resumeIngestFile } from "../src/api/ingestion";
import type { IngestSession, IngestFile } from "../src/api/types";

test("orden natural portable y agrupación por carpeta", () => {
  const names = ["p10.jpg", "p2.jpg", "p02.jpg", "P1.jpg", "àvia3.jpg", "àvia12.jpg"];
  expect(names.sort(naturalPathOrder)).toEqual(["P1.jpg", "p02.jpg", "p2.jpg", "p10.jpg", "àvia3.jpg", "àvia12.jpg"]);
  const files = ["Colección/A/1.jpg", "Colección/A/2.jpg", "Colección/B/1.jpg", "Colección/B/libro.pdf"].map((path) => ({
    name: path.split("/").pop()!, webkitRelativePath: path, size: 10, lastModified: 1,
  } as File));
  const plan = planCollection(files, {});
  expect(plan.map((item) => item.entry.position)).toEqual([1, 2, 1, 1]);
  expect(plan[0].entry.group).toBe("images:Colección/A");
  expect(plan[3].entry.group).toBe("pdf:Colección/B/libro.pdf");
});

test("planifica 100.000 imágenes sin multiplicar grupos ni perder posiciones", () => {
  const files = Array.from({ length: 100_000 }, (_, index) => ({ name: `${100_000 - index}.jpg`,
    webkitRelativePath: `Colección/Libro/${100_000 - index}.jpg`, size: 1_000_000, lastModified: 1 } as File));
  const plan = planCollection(files, {});
  expect(plan).toHaveLength(100_000);
  expect(new Set(plan.map((row) => row.entry.group)).size).toBe(1);
  expect(plan[0].entry.path).toBe("Colección/Libro/1.jpg");
  expect(plan[99_999].entry.position).toBe(100_000);
  expect(plan.reduce((sum, row) => sum + row.entry.byte_size, 0)).toBe(100_000_000_000);
});

test("reanudar comprueba los bytes locales y solo envía fragmentos ausentes", async () => {
  const originalFetch = globalThis.fetch;
  const file = new NodeFile(["abcdefghijkl"], "1.jpg", { lastModified: 1 }) as unknown as File;
  const session = { id: "session", chunk_bytes: 4 } as IngestSession;
  const item = { id: "file", byte_size: 12, last_modified: 1,
    parts: { "0": { sha256: createHash("sha256").update("abcd").digest("hex"), size: 4 } } } as IngestFile;
  const sent: string[] = [];
  globalThis.fetch = (async (url, options) => {
    if (options?.method === "PUT") sent.push(String(url));
    return new Response(JSON.stringify(item), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  try {
    await resumeIngestFile(session, item, file, new AbortController().signal, () => {});
    expect(sent.map((url) => url.split("/").pop())).toEqual(["4", "8"]);
    sent.length = 0;
    const changed = new NodeFile(["xxxxefghijkl"], "1.jpg", { lastModified: 1 }) as unknown as File;
    await expect(resumeIngestFile(session, item, changed, new AbortController().signal, () => {})).rejects.toThrow("no coincide");
    expect(sent).toHaveLength(0);
  } finally { globalThis.fetch = originalFetch; }
});
