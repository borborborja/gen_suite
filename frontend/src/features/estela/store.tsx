// Estela app state + actions, ported from the Component class in Estela.dc.html.
// Loads live linkage candidates from the API when available, falling back to sample data.
import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
  type ReactNode,
} from "react";
import { type Discovery } from "./data";
import { candidateToDiscovery } from "./mapCandidate";
import {
  confirmCandidate, rejectCandidate, acceptProposal, listCandidates,
} from "../../api/linkage";
import type { ThemeMode } from "./theme";

export type Nav =
  | "inicio" | "descubrimientos" | "arbol" | "persona"
  | "biblioteca" | "visor" | "buscar" | "super" | "familysearch" | "ajustes";
export type TreeView = "genograma" | "pedigree" | "abanico" | "lista";
export type Decision = "yes" | "no" | "skip";
export type ToastKind = "confirm" | "reject" | "skip" | "addrel" | null;
export type DiscSource = "sample" | "live";

interface Toast { show: boolean; msg: string; color: string; kind: ToastKind; prev: string | null }

interface EstelaState {
  theme: ThemeMode;
  nav: Nav;
  treeView: TreeView;
  selPerson: string;
  selDoc: string | null;
  selPage: number | null;
  discoveries: Discovery[];
  source: DiscSource;
  discIndex: number;
  decisions: Record<string, Decision>;
  panelOpen: boolean;
  added: Record<string, boolean>;
  zoom: boolean;
  toast: Toast;
  searchTab: string;
  visorHover: string;
}

interface EstelaCtx extends EstelaState {
  cur: Discovery;
  pendingCount: number;
  hasPending: boolean;
  allDone: boolean;
  go: (n: Nav) => void;
  openPerson: (id: string) => void;
  openDoc: (id: string, page?: number) => void;
  setTree: (v: TreeView) => void;
  toggleTheme: () => void;
  confirm: () => void;
  reject: () => void;
  skip: () => void;
  advance: () => void;
  jumpTo: (index: number) => void;
  reloadDiscoveries: () => Promise<void>;
  notify: (msg: string, color?: string) => void;
  addRel: (id: string) => void;
  addAllRel: () => void;
  undo: () => void;
  setZoom: (z: boolean) => void;
  setSearchTab: (t: string) => void;
  setVisorHover: (h: string) => void;
}

const Ctx = createContext<EstelaCtx | null>(null);

export function useEstela(): EstelaCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useEstela must be used inside <EstelaProvider>");
  return v;
}

export function EstelaProvider({ children, startTheme = "light" }: { children: ReactNode; startTheme?: ThemeMode }) {
  const [s, setS] = useState<EstelaState>({
    theme: startTheme,
    nav: "inicio",
    treeView: "genograma",
    selPerson: "joan",
    selDoc: null,
    selPage: null,
    // Start empty + live so no mock relatives ever show; reloadDiscoveries() fills from the backend.
    discoveries: [],
    source: "live",
    discIndex: 0,
    decisions: {},
    panelOpen: false,
    added: {},
    zoom: false,
    toast: { show: false, msg: "", color: "", kind: null, prev: null },
    searchTab: "archivo",
    visorHover: "a1",
  });

  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const disc = s.discoveries;
  const cur = disc[s.discIndex] ?? disc[0];
  const pendingCount = disc.filter((d) => !s.decisions[d.id]).length;
  const hasPending = disc.some((d) => !s.decisions[d.id]);
  // in live mode an empty queue means "nothing to review" (show the done state, not sample data)
  const allDone = s.source === "live" ? pendingCount === 0 : disc.length > 0 && disc.every((d) => s.decisions[d.id]);

  const reloadDiscoveries = useCallback(async () => {
    try {
      const cands = await listCandidates(undefined, "pending");
      // backend answered → switch to live (even if empty: shows a real empty state, not fake data)
      setS((p) => ({
        ...p, discoveries: cands.map(candidateToDiscovery), source: "live",
        discIndex: 0, decisions: {}, added: {}, panelOpen: false,
      }));
    } catch {
      /* backend unreachable → stay empty (no fake data), still in live mode */
    }
  }, []);

  useEffect(() => { void reloadDiscoveries(); }, [reloadDiscoveries]);

  const showToast = useCallback((msg: string, color: string, kind: ToastKind, prev: string | null) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setS((p) => ({ ...p, toast: { show: true, msg, color, kind, prev } }));
    toastTimer.current = setTimeout(() => setS((p) => ({ ...p, toast: { ...p.toast, show: false } })), 4200);
  }, []);

  const nextPending = useCallback((from: number, list: Discovery[], decisions: Record<string, Decision>) => {
    for (let i = from; i < list.length; i++) {
      if (!decisions[list[i].id]) return i;
    }
    return -1;
  }, []);

  const advance = useCallback(() => {
    setS((p) => {
      const nxt = nextPending(p.discIndex + 1, p.discoveries, p.decisions);
      if (nxt === -1) {
        const back = nextPending(0, p.discoveries, p.decisions);
        return { ...p, panelOpen: false, discIndex: back === -1 ? p.discIndex : back };
      }
      return { ...p, discIndex: nxt, panelOpen: false };
    });
  }, [nextPending]);

  const confirm = useCallback(async () => {
    const c = s.discoveries[s.discIndex];
    if (!c) return;
    if (c.candidateId) {
      try { await confirmCandidate(c.candidateId); }
      catch (err) { showToast("No se pudo confirmar: " + (err as Error).message, "var(--danger)", null, null); return; }
    }
    const dec: Record<string, Decision> = { ...s.decisions, [c.id]: "yes" };
    if (c.relatives && c.relatives.length) {
      setS((p) => ({ ...p, decisions: dec, panelOpen: true }));
      showToast("Confirmado. Añadido a tu árbol con su fuente.", "var(--ok)", "confirm", c.id);
    } else {
      setS((p) => ({ ...p, decisions: dec }));
      showToast("Confirmado y añadido a tu árbol, con su fuente.", "var(--ok)", "confirm", c.id);
      setTimeout(advance, 0);
    }
  }, [s.discIndex, s.decisions, s.discoveries, showToast, advance]);

  const reject = useCallback(async () => {
    const c = s.discoveries[s.discIndex];
    if (!c) return;
    if (c.candidateId) {
      try { await rejectCandidate(c.candidateId); }
      catch (err) { showToast("No se pudo descartar: " + (err as Error).message, "var(--danger)", null, null); return; }
    }
    setS((p) => ({ ...p, decisions: { ...p.decisions, [c.id]: "no" } }));
    showToast("Descartado. No se ha tocado tu árbol.", "var(--danger)", "reject", c.id);
    setTimeout(advance, 0);
  }, [s.discIndex, s.discoveries, showToast, advance]);

  const skip = useCallback(() => {
    const c = s.discoveries[s.discIndex];
    if (!c) return;
    setS((p) => ({ ...p, decisions: { ...p.decisions, [c.id]: "skip" } }));
    showToast("Marcado como «no lo sé». Volverá a la cola.", "var(--muted)", "skip", c.id);
    setTimeout(advance, 0);
  }, [s.discIndex, s.discoveries, showToast, advance]);

  const addRel = useCallback(async (id: string) => {
    const c = s.discoveries[s.discIndex];
    const rel = c?.relatives.find((r) => r.id === id);
    if (c?.candidateId && rel?.mentionId) {
      try { await acceptProposal(c.candidateId, rel.mentionId); }
      catch (err) { showToast("No se pudo añadir: " + (err as Error).message, "var(--danger)", null, null); return; }
    }
    setS((p) => ({ ...p, added: { ...p.added, [id]: true } }));
    showToast("Añadido a tu árbol como inferido, con su fuente.", "var(--warn)", "addrel", id);
  }, [s.discIndex, s.discoveries, showToast]);

  const addAllRel = useCallback(async () => {
    const c = s.discoveries[s.discIndex];
    if (!c) return;
    const ok: string[] = [];
    let failed = 0;
    for (const r of c.relatives) {
      if (c.candidateId && r.mentionId) {
        try { await acceptProposal(c.candidateId, r.mentionId); ok.push(r.id); }
        catch { failed++; }
      } else {
        ok.push(r.id);
      }
    }
    if (ok.length) setS((p) => { const add = { ...p.added }; ok.forEach((id) => (add[id] = true)); return { ...p, added: add }; });
    if (failed) showToast(`Añadidos ${ok.length}; ${failed} fallaron — reintenta.`, "var(--danger)", null, null);
    else showToast("Parientes añadidos como inferidos, con su fuente.", "var(--warn)", "addrel", null);
  }, [s.discIndex, s.discoveries, showToast]);

  const undo = useCallback(() => {
    setS((p) => {
      const t = p.toast;
      if (t.kind === "addrel" && t.prev) {
        const a = { ...p.added };
        delete a[t.prev];
        return { ...p, added: a, toast: { ...p.toast, show: false } };
      }
      if ((t.kind === "confirm" || t.kind === "reject" || t.kind === "skip") && t.prev) {
        const d = { ...p.decisions };
        delete d[t.prev];
        return { ...p, decisions: d, panelOpen: false, toast: { ...p.toast, show: false } };
      }
      return { ...p, toast: { ...p.toast, show: false } };
    });
  }, []);

  const jumpTo = useCallback((index: number) => setS((p) => ({ ...p, discIndex: index, panelOpen: false })), []);
  const notify = useCallback((msg: string, color = "var(--accent)") => showToast(msg, color, null, null), [showToast]);
  const go = useCallback((n: Nav) => setS((p) => ({ ...p, nav: n, zoom: false })), []);
  const openPerson = useCallback((id: string) => setS((p) => ({ ...p, selPerson: id, nav: "persona", zoom: false })), []);
  const openDoc = useCallback((id: string, page?: number) => setS((p) => ({ ...p, selDoc: id, selPage: page ?? null, nav: "visor", zoom: false })), []);
  const setTree = useCallback((v: TreeView) => setS((p) => ({ ...p, treeView: v })), []);
  const toggleTheme = useCallback(() => setS((p) => ({ ...p, theme: p.theme === "dark" ? "light" : "dark" })), []);
  const setZoom = useCallback((z: boolean) => setS((p) => ({ ...p, zoom: z })), []);
  const setSearchTab = useCallback((t: string) => setS((p) => ({ ...p, searchTab: t })), []);
  const setVisorHover = useCallback((h: string) => setS((p) => ({ ...p, visorHover: h })), []);

  // keyboard shortcuts (only on Descubrimientos)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (s.nav !== "descubrimientos") return;
      if (s.panelOpen && e.key.toLowerCase() !== "z") return;
      const k = e.key.toLowerCase();
      if (k === "s") { e.preventDefault(); confirm(); }
      else if (k === "n") { e.preventDefault(); reject(); }
      else if (e.key === " ") { e.preventDefault(); skip(); }
      else if (k === "z") { e.preventDefault(); setS((p) => ({ ...p, zoom: !p.zoom })); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [s.nav, s.panelOpen, confirm, reject, skip]);

  useEffect(() => () => { if (toastTimer.current) clearTimeout(toastTimer.current); }, []);

  const value = useMemo<EstelaCtx>(() => ({
    ...s, cur, pendingCount, hasPending, allDone,
    go, openPerson, openDoc, setTree, toggleTheme, confirm, reject, skip, advance, jumpTo, reloadDiscoveries, notify,
    addRel, addAllRel, undo, setZoom, setSearchTab, setVisorHover,
  }), [s, cur, pendingCount, hasPending, allDone, go, openPerson, openDoc, setTree, toggleTheme, confirm, reject, skip, advance, jumpTo, reloadDiscoveries, notify, addRel, addAllRel, undo, setZoom, setSearchTab, setVisorHover]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function confColors(level: string): { c: string; tint: string } {
  if (level === "ALTA") return { c: "var(--ok)", tint: "var(--ok-faint)" };
  if (level === "MEDIA") return { c: "var(--warn)", tint: "var(--warn-faint)" };
  return { c: "var(--terra)", tint: "rgba(181,83,42,0.08)" };
}

export function avatar(conf: string): { bg: string; fg: string } {
  if (conf === "confirmed") return { bg: "var(--ok)", fg: "#fff" };
  return { bg: "var(--warn-faint)", fg: "var(--warn)" };
}

export const colMap: Record<string, string> = {
  fg: "var(--fg)", ok: "var(--ok)", muted: "var(--muted)", warn: "var(--warn)",
};
