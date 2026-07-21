import type { CSSProperties } from "react";
import { useEstela, type Nav } from "./store";
import { fonts } from "./theme";
import type { Account } from "./EstelaApp";
import {
  HomeIcon, TreeIcon, SparkIcon, BookIcon, SearchIcon, GearIcon, SunIcon, MoonIcon, PinIcon,
} from "./icons";
import ActivityIndicator from "./ActivityIndicator";

function navItemStyle(active: boolean): CSSProperties {
  return {
    display: "flex", alignItems: "center", gap: 11, padding: "10px 12px", borderRadius: 9,
    cursor: "pointer", fontSize: 14, fontWeight: active ? 600 : 500,
    color: active ? "#fff" : "var(--muted)", background: active ? "var(--accent)" : "transparent",
  };
}

export default function Sidebar({ account }: { account?: Account }) {
  const e = useEstela();
  const isActive = (key: Nav) =>
    e.nav === key ||
    (key === "arbol" && e.nav === "persona") ||
    (key === "biblioteca" && e.nav === "visor");

  const items: { key: Nav; label: string; icon: JSX.Element }[] = [
    { key: "inicio", label: "Inicio", icon: <HomeIcon /> },
    { key: "arbol", label: "Mi árbol", icon: <TreeIcon /> },
    { key: "descubrimientos", label: "Descubrimientos", icon: <SparkIcon /> },
    { key: "biblioteca", label: "Biblioteca", icon: <BookIcon /> },
    { key: "lugares", label: "Lugares", icon: <PinIcon /> },
    { key: "buscar", label: "Buscar", icon: <SearchIcon /> },
    { key: "super", label: "Superdescubrimiento", icon: <SparkIcon /> },
    { key: "familysearch", label: "FamilySearch", icon: <BookIcon /> },
  ];

  return (
    <aside style={{ width: 248, flex: "none", height: "100vh", position: "sticky", top: 0, display: "flex", flexDirection: "column", borderRight: "1px solid var(--line)", background: "var(--surface)" }}>
      <div onClick={() => e.go("inicio")} style={{ display: "flex", alignItems: "center", gap: 11, padding: "22px 20px 18px", cursor: "pointer" }}>
        <div style={{ width: 34, height: 38, flex: "none", borderRadius: "6px 6px 3px 3px", background: "linear-gradient(165deg,var(--accent),var(--terra))", position: "relative", boxShadow: "0 3px 12px rgba(217,83,30,.4)" }}>
          <div style={{ position: "absolute", left: "50%", top: 7, transform: "translateX(-50%)", width: 13, height: 13, border: "2px solid rgba(255,255,255,.92)", borderRadius: "50%" }} />
          <div style={{ position: "absolute", left: "50%", top: 19, transform: "translateX(-50%)", width: 2, height: 13, background: "rgba(255,255,255,.92)" }} />
        </div>
        <div>
          <div style={{ fontFamily: fonts.serif, fontSize: 23, fontWeight: 600, letterSpacing: "-.01em", lineHeight: 1 }}>Estela</div>
          <div style={{ fontFamily: fonts.mono, fontSize: 9.5, letterSpacing: ".16em", color: "var(--muted)", marginTop: 3 }}>GEN_SUITE</div>
        </div>
      </div>

      <nav style={{ display: "flex", flexDirection: "column", gap: 3, padding: "6px 12px", flex: 1 }}>
        {items.map((it) => (
          <div key={it.key} onClick={() => e.go(it.key)} style={navItemStyle(isActive(it.key))}>
            {it.icon}
            <span style={{ flex: it.key === "descubrimientos" ? 1 : undefined }}>{it.label}</span>
            {it.key === "descubrimientos" && e.hasPending && (
              <span style={{ fontFamily: fonts.mono, fontSize: 11, fontWeight: 500, background: "var(--accent)", color: "#fff", borderRadius: 999, minWidth: 20, height: 20, display: "inline-flex", alignItems: "center", justifyContent: "center", padding: "0 6px" }}>
                {e.pendingCount}
              </span>
            )}
          </div>
        ))}
      </nav>

      <div style={{ padding: 12, borderTop: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 3 }}>
        <ActivityIndicator />
        <div onClick={() => e.go("ajustes")} style={navItemStyle(e.nav === "ajustes")}>
          <GearIcon size={18} />
          <span>Ajustes</span>
        </div>
        <div onClick={e.toggleTheme} style={{ display: "flex", alignItems: "center", gap: 11, padding: "9px 12px", borderRadius: 8, cursor: "pointer", color: "var(--muted)", fontSize: 13.5, fontWeight: 500 }}>
          {e.theme === "dark" ? <SunIcon size={18} /> : <MoonIcon size={18} />}
          <span>{e.theme === "dark" ? "Modo claro" : "Modo oscuro"}</span>
        </div>
        {account && (
          <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", marginTop: 2, borderTop: "1px solid var(--line2)" }}>
            <div style={{ width: 26, height: 26, borderRadius: "50%", flex: "none", background: "var(--accent-faint)", color: "var(--accent)", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: fonts.serif, fontWeight: 600, fontSize: 12 }}>
              {account.email.slice(0, 1).toUpperCase()}
            </div>
            <span title={account.email} style={{ flex: 1, minWidth: 0, fontSize: 12, color: "var(--muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{account.email}</span>
            <span onClick={account.onLogout} title="Salir" style={{ cursor: "pointer", color: "var(--muted)", fontSize: 12, fontWeight: 600 }}>Salir</span>
          </div>
        )}
      </div>
    </aside>
  );
}
