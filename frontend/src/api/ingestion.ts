import { api, authFetch } from "./client";
import type { IngestBook, IngestEntry, IngestFile, IngestOptions, IngestSession } from "./types";
import type { IngestCleanup, IngestCleanupPreview } from "./types";

const base = "/documents/ingestion";
export const listIngestSessions = (offset = 0, inactiveDays?: number) => api<IngestSession[]>(`${base}?offset=${offset}${inactiveDays ? `&inactive_days=${inactiveDays}` : ""}`);
export const getIngestCleanup = (id: string) => api<IngestCleanup | null>(`${base}/${id}/cleanup`);
export const previewIngestCleanup = (id: string) => api<IngestCleanupPreview>(`${base}/${id}/cleanup/preview`, { method: "POST" });
export const approveIngestCleanup = (id: string, body: { preview_hash: string; reason: string; authorize_permanent_staging_deletion: true; local_originals_confirmed: true }) =>
  api<IngestCleanup>(`${base}/${id}/cleanup`, { method: "POST", body: JSON.stringify(body) });
export const retryIngestCleanup = (id: string) => api<IngestCleanup>(`${base}/${id}/cleanup/retry`, { method: "POST" });
export const getIngestSession = (id: string) => api<IngestSession>(`${base}/${id}`);
export const createIngestSession = (body: { client_id: string; title: string; expected_files: number; reserved_bytes: number; options: IngestOptions }, signal?: AbortSignal) =>
  api<IngestSession>(base, { method: "POST", body: JSON.stringify(body), signal });
export const appendIngestManifest = (id: string, files: IngestEntry[], signal?: AbortSignal) =>
  api<IngestFile[]>(`${base}/${id}/manifest`, { method: "POST", body: JSON.stringify({ files }), signal });
export const sealIngestManifest = (id: string, signal?: AbortSignal) => api<IngestSession>(`${base}/${id}/seal`, { method: "POST", signal });
export const completeIngestSession = (id: string, signal?: AbortSignal) => api<IngestSession>(`${base}/${id}/complete`, { method: "POST", signal });
export const getIngestFile = (id: string, fileId: string, signal?: AbortSignal) => api<IngestFile>(`${base}/${id}/files/${fileId}`, { signal });
export const listIngestBooks = (id: string, after?: string) => api<IngestBook[]>(`${base}/${id}/books?limit=1000${after ? `&after=${after}` : ""}`);

export async function putIngestChunk(id: string, fileId: string, offset: number, data: ArrayBuffer, checksum: string, signal?: AbortSignal) {
  const response = await authFetch(`${base}/${id}/files/${fileId}/chunks/${offset}`, {
    method: "PUT", body: data, headers: { "Content-Type": "application/octet-stream", "X-Content-SHA256": checksum }, signal,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: `HTTP ${response.status}` }));
    throw new Error(typeof error.detail === "string" ? error.detail : JSON.stringify(error.detail));
  }
  return response.json() as Promise<IngestFile>;
}

export async function resumeIngestFile(session: IngestSession, item: IngestFile, file: File,
  signal: AbortSignal, progress: (bytesSent: number, offset: number) => void) {
  if (!globalThis.crypto?.subtle) throw new Error("La comprobación de integridad requiere HTTPS o localhost");
  const saved = await getIngestFile(session.id, item.id, signal);
  if (file.size !== saved.byte_size || file.lastModified !== saved.last_modified) throw new Error(`El archivo cambió: ${saved.path}`);
  for (let offset = 0; offset < file.size; offset += session.chunk_bytes) {
    signal.throwIfAborted();
    const data = await file.slice(offset, offset + session.chunk_bytes).arrayBuffer();
    const digest = await crypto.subtle.digest("SHA-256", data);
    const hash = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
    const old = saved.parts?.[String(offset)];
    if (old) {
      if (old.sha256 !== hash || old.size !== data.byteLength) throw new Error(`El original local no coincide con el fragmento guardado: ${saved.path}. Crea una sesión nueva.`);
      progress(0, offset + data.byteLength);
    } else {
      await putIngestChunk(session.id, saved.id, offset, data, hash, signal);
      progress(data.byteLength, offset + data.byteLength);
    }
  }
}

// Portable ordering shared with the server-folder importer: ASCII case-insensitive,
// numeric runs compared as integers, UTF-16 lexical tie-breaks (no host locale dependency).
export function naturalPathOrder(a: string, b: string): number {
  const tokens = (value: string) => value.replace(/[A-Z]/g, (c) => c.toLowerCase()).split(/([0-9]+)/);
  const left = tokens(a), right = tokens(b);
  for (let i = 0; i < Math.min(left.length, right.length); i++) {
    if (i % 2) {
      const x = BigInt(left[i]), y = BigInt(right[i]);
      if (x !== y) return x < y ? -1 : 1;
    } else if (left[i] !== right[i]) return left[i] < right[i] ? -1 : 1;
  }
  if (left.length !== right.length) return left.length - right.length;
  return a === b ? 0 : a < b ? -1 : 1;
}

export function planCollection(files: File[], defaults: IngestOptions, savedBooks: IngestBook[] = []): { file: File; entry: IngestEntry }[] {
  if (!files.length || files.length > 100_000) throw new Error("Selecciona entre 1 y 100.000 archivos");
  const previous = new Map(savedBooks.map((book) => [book.group, book]));
  const positions = new Map<string, number>();
  const paths = new Set<string>();
  return [...files].sort((a, b) => naturalPathOrder(a.webkitRelativePath || a.name, b.webkitRelativePath || b.name))
    .map((file) => {
      const path = file.webkitRelativePath || file.name;
      if (paths.has(path)) throw new Error(`Ruta duplicada: ${path}. Utiliza el selector de carpetas para conservar el origen.`);
      paths.add(path);
      const pdf = /\.pdf$/i.test(file.name);
      const folder = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "Imágenes seleccionadas";
      const group = pdf ? `pdf:${path}` : `images:${folder}`;
      const position = (positions.get(group) || 0) + 1;
      positions.set(group, position);
      const old = previous.get(group);
      return { file, entry: { path, group, title: old?.title || (pdf ? file.name.replace(/\.pdf$/i, "") : folder).slice(0, 512),
        kind: pdf ? "pdf" : "image_set", position, byte_size: file.size, last_modified: file.lastModified,
        options: old?.options || defaults } };
    });
}
