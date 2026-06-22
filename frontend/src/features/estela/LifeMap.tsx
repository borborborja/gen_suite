import { useEffect, useRef } from "react";

// Leaflet is loaded lazily from the CDN (OSM tiles already require network, so no new bundle dep).
declare global { interface Window { L?: any } }

let leafletPromise: Promise<any> | null = null;
function loadLeaflet(): Promise<any> {
  if (window.L) return Promise.resolve(window.L);
  if (leafletPromise) return leafletPromise;
  leafletPromise = new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
    document.head.appendChild(css);
    const js = document.createElement("script");
    js.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
    js.onload = () => resolve(window.L);
    js.onerror = reject;
    document.head.appendChild(js);
  });
  return leafletPromise;
}

export interface MapPoint { lat: number; lng: number; label: string; sub?: string }

export default function LifeMap({ points }: { points: MapPoint[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);

  useEffect(() => {
    let cancelled = false;
    loadLeaflet().then((L) => {
      if (cancelled || !ref.current) return;
      if (!mapRef.current) {
        mapRef.current = L.map(ref.current, { scrollWheelZoom: false });
        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          attribution: "© OpenStreetMap", maxZoom: 18,
        }).addTo(mapRef.current);
      }
      const map = mapRef.current;
      map.eachLayer((ly: any) => { if (ly instanceof L.Marker) map.removeLayer(ly); });
      const latlngs: [number, number][] = [];
      points.forEach((p) => {
        const m = L.marker([p.lat, p.lng]).addTo(map);
        m.bindPopup(`<b>${p.label}</b>${p.sub ? `<br/>${p.sub}` : ""}`);
        latlngs.push([p.lat, p.lng]);
      });
      if (latlngs.length === 1) map.setView(latlngs[0], 9);
      else if (latlngs.length > 1) map.fitBounds(latlngs, { padding: [30, 30] });
      setTimeout(() => map.invalidateSize(), 100);
    }).catch(() => { /* offline / blocked — section stays hidden by caller */ });
    return () => { cancelled = true; };
  }, [points]);

  useEffect(() => () => { if (mapRef.current) { mapRef.current.remove(); mapRef.current = null; } }, []);

  return <div ref={ref} style={{ width: "100%", height: 320, borderRadius: 10, overflow: "hidden" }} />;
}
