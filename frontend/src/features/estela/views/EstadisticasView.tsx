import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { fonts } from "../theme";
import { getStatistics, type TreeStatistics, type CountItem } from "../../../api/tree";

const SEX_LABEL: Record<string, string> = { M: "Hombres", F: "Mujeres", U: "Sin definir" };
const ROMAN: Record<number, string> = {
  1200: "XIII", 1300: "XIV", 1400: "XV", 1500: "XVI", 1600: "XVII",
  1700: "XVIII", 1800: "XIX", 1900: "XX", 2000: "XXI",
};
const centuryLabel = (c: number) => `s. ${ROMAN[c] ?? String(c / 100 + 1)}`;

const card: CSSProperties = { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 14, padding: 22 };

export default function EstadisticasView() {
  const [s, setS] = useState<TreeStatistics | null>(null);
  const [err, setErr] = useState(false);
  useEffect(() => { getStatistics().then(setS).catch(() => setErr(true)); }, []);

  if (err) return <section style={{ padding: "32px 44px" }}><p style={{ color: "var(--muted)" }}>No se pudieron cargar las estadísticas.</p></section>;
  if (!s) return <section style={{ padding: "32px 44px" }}><p style={{ color: "var(--muted)" }}>Cargando…</p></section>;

  const tiles: { label: string; value: string }[] = [
    { label: "Personas", value: s.totals.persons.toLocaleString() },
    { label: "Familias", value: s.totals.families.toLocaleString() },
    { label: "Eventos", value: s.totals.events.toLocaleString() },
    { label: "Lugares", value: s.totals.places.toLocaleString() },
    { label: "Hijos por familia", value: s.avg_children_per_family ? s.avg_children_per_family.toFixed(1) : "—" },
  ];
  const sexItems: CountItem[] = Object.entries(s.sex)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => ({ label: SEX_LABEL[k] ?? k, count: v }));

  return (
    <section style={{ padding: "32px 44px 64px", maxWidth: 1080 }}>
      <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 34, margin: 0, letterSpacing: "-.02em" }}>Estadísticas</h1>
      <p style={{ color: "var(--muted)", fontSize: 14, margin: "6px 0 24px" }}>Una radiografía de tu árbol: de dónde vienen, cuándo nacieron y cuánto vivieron.</p>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 14, marginBottom: 22 }}>
        {tiles.map((t) => (
          <div key={t.label} style={{ ...card, padding: "18px 20px" }}>
            <div style={{ fontFamily: fonts.serif, fontSize: 28, fontWeight: 600, lineHeight: 1 }}>{t.value}</div>
            <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>{t.label}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 18 }}>
        <ChartCard title="Apellidos más frecuentes" sub="nombre principal de cada persona">
          <Bars items={s.surnames} />
        </ChartCard>
        <ChartCard title="Nacimientos por década" sub="personas con año de nacimiento conocido">
          <Bars items={s.birth_decades} fmtLabel={(l) => `${l}s`} />
        </ChartCard>
        <ChartCard title="Lugares con más eventos" sub="nacimientos, bodas, defunciones…">
          <Bars items={s.places} />
        </ChartCard>
        <ChartCard title="Esperanza de vida" sub="media de años vividos, por siglo de nacimiento (solo personas con nacimiento y defunción)">
          <Bars
            items={s.lifespan_by_century.map((l) => ({ label: centuryLabel(l.century), count: l.avg_years }))}
            max={100}
            fmtValue={(v) => `${v} años`}
          />
        </ChartCard>
        <ChartCard title="Distribución por sexo" sub="según consta en el árbol">
          <Bars items={sexItems} />
        </ChartCard>
      </div>
    </section>
  );
}

function ChartCard({ title, sub, children }: { title: string; sub: string; children: ReactNode }) {
  return (
    <div style={card}>
      <h3 style={{ margin: 0, fontSize: 15.5, fontWeight: 600 }}>{title}</h3>
      <p style={{ margin: "3px 0 16px", fontSize: 12, color: "var(--muted)" }}>{sub}</p>
      {children}
    </div>
  );
}

/** Barras horizontales de una sola serie: etiqueta en tinta, barra en --accent, valor al final. */
function Bars({ items, max, fmtLabel, fmtValue }: {
  items: CountItem[];
  max?: number;
  fmtLabel?: (l: string) => string;
  fmtValue?: (v: number) => string;
}) {
  if (items.length === 0) return <p style={{ color: "var(--muted)", fontSize: 13, margin: 0 }}>Sin datos suficientes.</p>;
  const top = max ?? Math.max(...items.map((i) => i.count));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 7 }}>
      {items.map((it) => (
        <div key={it.label} title={`${fmtLabel?.(it.label) ?? it.label}: ${fmtValue?.(it.count) ?? it.count.toLocaleString()}`}
          style={{ display: "grid", gridTemplateColumns: "110px 1fr 52px", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 12.5, color: "var(--fg)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{fmtLabel?.(it.label) ?? it.label}</span>
          <div style={{ height: 14, background: "var(--bg)", borderRadius: 4, overflow: "hidden" }}>
            <div style={{ width: `${top > 0 ? Math.max(2, (it.count / top) * 100) : 0}%`, height: "100%", background: "var(--accent)", borderRadius: 4 }} />
          </div>
          <span style={{ fontFamily: fonts.mono, fontSize: 11.5, color: "var(--muted)", textAlign: "right" }}>{fmtValue?.(it.count) ?? it.count.toLocaleString()}</span>
        </div>
      ))}
    </div>
  );
}
