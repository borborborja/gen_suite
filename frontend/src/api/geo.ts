import { api } from "./client";

export interface GeoResult {
  name: string;
  display_name: string;
  lat: number;
  lng: number;
  country: string | null;
  type: string | null;
}

export const geoSearch = (q: string) =>
  api<GeoResult[]>(`/geo/search?q=${encodeURIComponent(q)}`);
