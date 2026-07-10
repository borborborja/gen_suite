import { api, authFetch } from "./client";

export interface MediaItem {
  id: string;
  person_id: string;
  caption: string | null;
  is_primary: boolean;
}

export const listMedia = (personId: string) =>
  api<MediaItem[]>(`/persons/${personId}/media`);

export const updateMedia = (mediaId: string, body: { caption?: string | null; make_primary?: boolean }) =>
  api<MediaItem>(`/media/${mediaId}`, { method: "PATCH", body: JSON.stringify(body) });

export const deleteMedia = (mediaId: string) =>
  api<void>(`/media/${mediaId}`, { method: "DELETE" });

export async function uploadMedia(personId: string, file: File, caption?: string): Promise<MediaItem> {
  const fd = new FormData();
  fd.append("file", file);
  if (caption) fd.append("caption", caption);
  const res = await authFetch(`/persons/${personId}/media`, { method: "POST", body: fd });
  if (!res.ok) throw new Error((await res.text()) || `${res.status}`);
  return res.json();
}

// Streams the (private) blob with the auth header → object URL for <img src>.
export async function mediaObjectUrl(mediaId: string): Promise<string> {
  const res = await authFetch(`/media/${mediaId}/raw`);
  if (!res.ok) throw new Error(`${res.status}`);
  return URL.createObjectURL(await res.blob());
}
