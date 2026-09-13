// Estela app state + actions, ported from the Component class in Estela.dc.html.
// Loads live linkage candidates from the API when available, falling back to sample data.
import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
  type ReactNode,
} from "react";
import { type Discovery } from "./data";
import { candidateToDiscovery } from "./mapCandidate";
import {
  applyCandidate, previewCandidate, rejectCandidate, listCandidates,
} from "../../api/linkage";
import { revertChange } from "../../api/tree";
import type { ThemeMode } from "./theme";

export type Nav =
  | "inicio" | "descubrimientos" | "arbol" | "persona"
  | "biblioteca" | "visor" | "buscar" | "super" | "familysearch" | "ajustes"
  | "lugares" | "historial" | "estadisticas" | "consistencia" | "investigacion" | "compartir" | "sincronizacion";
export type TreeView = "genograma" | "pedigree" | "abanico" | "descendientes" | "lista";
export type Decision = "yes" | "no" | "skip";
export type ToastKind = "confirm" | "reject" | "skip" | "addrel" | null;
export type DiscSource = "sample" | "live";

interface Toast { show: boolean; msg: string; color: string; kind: ToastKind; changeId: string | null }

interface EstelaState {
  theme: ThemeMode;
  nav: Nav;
  treeView: TreeView;
  selPerson: string;
  selDoc: string | null;
  selPage: number | null;
  discoveries: Discovery[];
  source: DiscSource;
  discError: boolean; // backend unreachable on last reload — distinguish from a real empty queue
  discIndex: number;
  decisions: Record<string, Decision>;
  panelOpen: boolean;
  reviewToken: string | null;
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
  confirm: () => Promise<void>;
  reject: () => Promise<void>;
  skip: () => void;
  advance: () => void;
  jumpTo: (index: number) => void;
  reloadDiscoveries: () => Promise<void>;
  notify: (msg: string, color?: string) => void;
  notifyChange: (msg: string, changeId: string, color?: string) => void;
  addRel: (id: string) => Promise<void>;
  addAllRel: () => Promise<void>;
  applyReview: () => Promise<void>;
  undo: () => Promise<void>;
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
    selPerson: "", // set via openPerson() with a real person id
    selDoc: null,
    selPage: null,
    // Start empty + live so no mock relatives ever show; reloadDiscoveries() fills from the backend.
    discoveries: [],
    source: "live",
    discError: false,
    discIndex: 0,
    decisions: {},
    panelOpen: false,
    reviewToken: null,
    added: {},
    zoom: false,
    toast: { show: false, msg: "", color: "", kind: null, changeId: null },
    searchTab: "archivo",
    visorHover: "a1",
  });

  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const disc = s.discoveries;
  const cur = disc[s.discIndex] ?? disc[0];
  // "skip" is NOT a final decision — a skipped discovery stays in the queue (the toast promises
  // "volverá a la cola"); only yes/no settle an item.
  const isDecided = (d?: Decision) => d === "yes" || d === "no";
  const pendingCount = disc.filter((d) => !isDecided(s.decisions[d.id])).length;
  const hasPending = pendingCount > 0;
  // in live mode an empty queue means "nothing to review" (show the done state, not sample data)
  const allDone = s.source === "live"
    ? pendingCount === 0 && !s.discError
    : disc.length > 0 && disc.every((d) => isDecided(s.decisions[d.id]));

  const reloadDiscoveries = useCallback(async () => {
    try {
      const cands = await listCandidates(undefined, "pending");
      // backend answered → switch to live (even if empty: shows a real empty state, not fake data)
      setS((p) => ({
        ...p, discoveries: cands.map(candidateToDiscovery), source: "live", discError: false,
        discIndex: 0, decisions: {}, added: {}, panelOpen: false, reviewToken: null,
      }));
    } catch {
      // backend unreachable → flag it so the UI shows an error + retry, not "todo revisado"
      setS((p) => ({ ...p, discError: true }));
    }
  }, []);

  useEffect(() => { void reloadDiscoveries(); }, [reloadDiscoveries]);

  const showToast = useCallback((msg: string, color: string, kind: ToastKind, changeId: string | null = null) => {
    if (toastTimer.current) clearTimeout(toastTimer.current);
    setS((p) => ({ ...p, toast: { show: true, msg, color, kind, changeId } }));
    toastTimer.current = setTimeout(() => setS((p) => ({ ...p, toast: { ...p.toast, show: false } })), 4200);
  }, []);

  const nextPending = useCallback((from: number, list: Discovery[], decisions: Record<string, Decision>) => {
    // prefer never-seen items; fall back to skipped ones (they stay in the queue)
    for (let i = from; i < list.length; i++) {
      if (!decisions[list[i].id]) return i;
    }
    for (let i = from; i < list.length; i++) {
      if (decisions[list[i].id] === "skip") return i;
    }
    return -1;
  }, []);

  const advance = useCallback(() => {
    setS((p) => {
      const nxt = nextPending(p.discIndex + 1, p.discoveries, p.decisions);
      if (nxt === -1) {
        const back = nextPending(0, p.discoveries, p.decisions);
        return { ...p, panelOpen: false, reviewToken: null, discIndex: back === -1 ? p.discIndex : back };
      }
      return { ...p, discIndex: nxt, panelOpen: false, reviewToken: null };
    });
  }, [nextPending]);

  const confirm = useCallback(async () => {
    const c = s.discoveries[s.discIndex];
    if (!c) return;
    if (c.candidateId) {
      try {
        const preview = await previewCandidate(c.candidateId);
        setS((p) => ({ ...p, panelOpen: true, reviewToken: preview.preview_token, added: {} }));
        showToast("Revisa los cambios antes de aplicarlos al árbol.", "var(--warn)", null);
      } catch (err) {
        showToast("No se pudo preparar la revisión: " + (err as Error).message, "var(--danger)", null);
      }
      return;
    }
    showToast("Esta propuesta no tiene una decisión persistente asociada.", "var(--danger)", null);
  }, [s.discIndex, s.discoveries, showToast]);

  const reject = useCallback(async () => {
    const c = s.discoveries[s.discIndex];
    if (!c) return;
    let changeId: string | null = null;
    if (c.candidateId) {
      try { changeId = (await rejectCandidate(c.candidateId)).change_id ?? null; }
      catch (err) { showToast("No se pudo descartar: " + (err as Error).message, "var(--danger)", null); return; }
    }
    setS((p) => ({ ...p, decisions: { ...p.decisions, [c.id]: "no" } }));
    showToast("Descartado. No se ha tocado tu árbol.", "var(--danger)", "reject", changeId);
    setTimeout(advance, 0);
  }, [s.discIndex, s.discoveries, showToast, advance]);

  const skip = useCallback(() => {
    const c = s.discoveries[s.discIndex];
    if (!c) return;
    setS((p) => ({ ...p, decisions: { ...p.decisions, [c.id]: "skip" } }));
    showToast("Marcado como «no lo sé». Volverá a la cola.", "var(--muted)", "skip");
    setTimeout(advance, 0);
  }, [s.discIndex, s.discoveries, showToast, advance]);

  const addRel = useCallback(async (id: string) => {
    if (!s.reviewToken) return;
    setS((p) => ({ ...p, added: { ...p.added, [id]: !p.added[id] } }));
  }, [s.reviewToken]);

  const addAllRel = useCallback(async () => {
    const c = s.discoveries[s.discIndex];
    if (!c || !s.reviewToken) return;
    setS((p) => ({ ...p, added: Object.fromEntries(c.relatives.map((r) => [r.id, true])) }));
  }, [s.discIndex, s.discoveries, s.reviewToken]);

  const applyReview = useCallback(async () => {
    const c = s.discoveries[s.discIndex];
    if (!c?.candidateId || !s.reviewToken) return;
    const relative_mention_ids = c.relatives.filter((r) => s.added[r.id]).map((r) => r.mentionId).filter(Boolean) as string[];
    try {
      const result = await applyCandidate(c.candidateId, { preview_token: s.reviewToken, relative_mention_ids });
      setS((p) => ({ ...p, decisions: { ...p.decisions, [c.id]: "yes" }, panelOpen: false, reviewToken: null }));
      showToast("Propuesta aplicada al árbol con sus fuentes.", "var(--ok)", "confirm", result.change_id);
      setTimeout(advance, 0);
    } catch (err) {
      showToast("No se pudo aplicar: " + (err as Error).message, "var(--danger)", null);
    }
  }, [s.discIndex, s.discoveries, s.reviewToken, s.added, showToast, advance]);

  const undo = useCallback(async () => {
    const changeId = s.toast.changeId;
    if (!changeId) { setS((p) => ({ ...p, toast: { ...p.toast, show: false } })); return; }
    try {
      await revertChange(changeId);
      await reloadDiscoveries();
      showToast("Cambio deshecho en el árbol.", "var(--ok)", null);
    } catch (err) {
      showToast("No se pudo deshacer: " + (err as Error).message, "var(--danger)", null);
    }
  }, [s.toast.changeId, reloadDiscoveries, showToast]);

  const jumpTo = useCallback((index: number) => setS((p) => ({ ...p, discIndex: index, panelOpen: false, reviewToken: null })), []);
  const notify = useCallback((msg: string, color = "var(--accent)") => showToast(msg, color, null), [showToast]);
  const notifyChange = useCallback((msg: string, changeId: string, color = "var(--accent)") => showToast(msg, color, "confirm", changeId), [showToast]);
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
    go, openPerson, openDoc, setTree, toggleTheme, confirm, reject, skip, advance, jumpTo, reloadDiscoveries, notify, notifyChange,
    addRel, addAllRel, applyReview, undo, setZoom, setSearchTab, setVisorHover,
  }), [s, cur, pendingCount, hasPending, allDone, go, openPerson, openDoc, setTree, toggleTheme, confirm, reject, skip, advance, jumpTo, reloadDiscoveries, notify, notifyChange, addRel, addAllRel, applyReview, undo, setZoom, setSearchTab, setVisorHover]);

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
