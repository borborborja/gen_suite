import { api } from "./client";
import type { StorageInventory, StorageInventorySummary } from "./types";

export const listStorageInventories = (offset = 0) => api<StorageInventorySummary[]>(`/storage/inventories?offset=${offset}`);
export const getStorageInventory = (id: string) => api<StorageInventory>(`/storage/inventories/${id}`);
export const createStorageInventory = (body: { client_id: string; reason: string }) =>
  api<StorageInventory>("/storage/inventories", { method: "POST", body: JSON.stringify(body) });
export const controlStorageInventory = (id: string, action: "cancel" | "retry") =>
  api<StorageInventory>(`/storage/inventories/${id}/${action}`, { method: "POST" });
