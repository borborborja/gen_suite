import { api } from "./client";

export interface Capabilities {
  familysearch: boolean;
  gramps_sync: boolean;
  external_archive_search: "manual_links";
}

export const getCapabilities = () => api<Capabilities>("/capabilities");
