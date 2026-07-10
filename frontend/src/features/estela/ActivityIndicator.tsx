import { useCallback, useEffect, useRef, useState } from "react";
import { useEstela } from "./store";
import { fonts } from "./theme";
import { listJobs, cancelJob, type JobItem } from "../../api/jobs";

import { jobLabel as label, STATUS_COLOR } from "./labels";

const RUNNING = new Set(["running", "queued"]);
const TERMINAL: Record<string, string> = { completed: "completado", error: "falló", cancelled: "cancelado" };

function ago(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "hace un momento";
  if (s < 3600) return `hace ${Math.floor(s / 60)} min`;
  if (s < 86400) return `hace ${Math.floor(s / 3600)} h`;
  return `hace ${Math.floor(s / 86400)} d`;
}

// A queued/running job that started a while ago and never reached a terminal state is likely
// stuck (worker dead, or nothing consuming the queue). We surface that so the user can cancel it.
const STUCK_MIN = 15;
const ageMin = (iso: string) => (Date.now() - new Date(iso).getTime()) / 60000;
const isStuck = (j: JobItem) => RUNNING.has(j.status) && ageMin(j.created_at) > STUCK_MIN;

export default function ActivityIndicator() {
  const e = useEstela();
  const notify = e.notify; // stable (useCallback in the store) — safe effect dependency
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [open, setOpen] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const prev = useRef<Map<string, string> | null>(null); // job id -> last status; null until first load

  const refresh = useCallback(async () => {
    let list: JobItem[];
    try { list = await listJobs(); } catch { return; }
    const seen = prev.current;
    if (seen) {
      for (const j of list) {
        const before = seen.get(j.id);
        if (before && RUNNING.has(before) && TERMINAL[j.status]) {
          const color = j.status === "completed" ? "var(--ok)" : j.status === "error" ? "var(--danger)" : "var(--muted)";
          const reason = j.status === "error" && j.error ? ` — ${j.error.slice(0, 140)}` : "";
          notify(`${label(j.type)}: ${TERMINAL[j.status]}${reason}`, color);
        }
      }
    }
    prev.current = new Map(list.map((j) => [j.id, j.status]));
    setJobs(list);
  }, [notify]);
  useEffect(() => {
    let alive = true;
    const tick = () => { if (alive) void refresh(); };
    tick();
    const id = setInterval(tick, 4000);
    return () => { alive = false; clearInterval(id); };
  }, [refresh]);

  async function cancel(id: string) {
    try { await cancelJob(id); await refresh(); e.notify("Tarea cancelada", "var(--muted)"); }
    catch (err) { e.notify((err as Error).message, "var(--danger)"); }
  }

  const active = jobs.filter((j) => RUNNING.has(j.status));
  const stuck = jobs.filter(isStuck).length;
  const recent = jobs.slice(0, 8);

  return (
    <div style={{ position: "relative" }}>
      <div onClick={() => setOpen((v) => !v)} style={{ display: "flex", alignItems: "center", gap: 11, padding: "9px 12px", borderRadius: 8, cursor: "pointer", color: "var(--muted)", fontSize: 13.5, fontWeight: 500 }}>
        <span style={{ width: 18, display: "inline-flex", justifyContent: "center" }}>
          {active.length > 0
            ? <span style={{ width: 9, height: 9, borderRadius: "50%", background: "var(--accent)", animation: "estPulse 1.4s infinite" }} />
            : <span style={{ width: 9, height: 9, borderRadius: "50%", background: "var(--ok)" }} />}
        </span>
        <span style={{ flex: 1 }}>{active.length > 0 ? `Tareas en curso` : "Tareas"}{stuck > 0 ? " ⚠" : ""}</span>
        {active.length > 0 && (
          <span style={{ fontFamily: fonts.mono, fontSize: 11, fontWeight: 500, background: stuck > 0 ? "var(--danger)" : "var(--accent)", color: "#fff", borderRadius: 999, minWidth: 20, height: 20, display: "inline-flex", alignItems: "center", justifyContent: "center", padding: "0 6px" }}>{active.length}</span>
        )}
      </div>

      {open && (
        <div style={{ position: "absolute", bottom: "calc(100% + 6px)", left: 0, right: 0, zIndex: 60, background: "var(--elevated)", border: "1px solid var(--line)", borderRadius: 12, boxShadow: "var(--shadow)", padding: 12, maxHeight: 360, overflowY: "auto" }}>
          <div style={{ fontFamily: fonts.mono, fontSize: 10, letterSpacing: ".12em", color: "var(--muted)", marginBottom: 8, textTransform: "uppercase" }}>Procesos en segundo plano</div>
          {recent.length === 0 && <div style={{ color: "var(--muted)", fontSize: 12.5 }}>Sin procesos. Sube o transcribe un libro.</div>}
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {recent.map((j) => {
              const p = j.progress as { done?: number; total?: number } | null;
              const running = RUNNING.has(j.status);
              const stuckJob = isStuck(j);
              const expanded = sel === j.id;
              return (
                <div key={j.id} style={{ borderBottom: "1px solid var(--line2)" }}>
                  <div onClick={() => setSel(expanded ? null : j.id)} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", cursor: "pointer" }}>
                    <span style={{ width: 8, height: 8, borderRadius: "50%", flex: "none", background: stuckJob ? "var(--danger)" : (STATUS_COLOR[j.status] ?? "var(--muted)"), ...(running && !stuckJob ? { animation: "estPulse 1.4s infinite" } : {}) }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 12.5 }}>{label(j.type)}</div>
                      <div style={{ fontFamily: fonts.mono, fontSize: 10, color: stuckJob ? "var(--danger)" : (STATUS_COLOR[j.status] ?? "var(--muted)") }}>
                        {running && p ? `${p.done ?? 0}/${p.total ?? "?"}` : (TERMINAL[j.status] ?? j.status)}{stuckJob ? " · atascada" : ""}
                      </div>
                    </div>
                    <span style={{ color: "var(--muted)", fontSize: 11 }}>{expanded ? "▾" : "▸"}</span>
                  </div>
                  {expanded && (
                    <div style={{ padding: "0 0 10px 18px", display: "flex", flexDirection: "column", gap: 6 }}>
                      <div style={{ fontFamily: fonts.mono, fontSize: 10, color: "var(--muted)" }}>iniciada {ago(j.created_at)}{j.finished_at ? ` · terminó ${ago(j.finished_at)}` : ""}</div>
                      {j.error && <div style={{ fontSize: 11.5, color: "var(--danger)", lineHeight: 1.4 }}>{j.error}</div>}
                      {stuckJob && !j.error && <div style={{ fontSize: 11.5, color: "var(--danger)" }}>Lleva {Math.round(ageMin(j.created_at))} min sin terminar — probablemente el worker se interrumpió.</div>}
                      {running && (
                        <button onClick={(ev) => { ev.stopPropagation(); cancel(j.id); }} style={{ alignSelf: "flex-start", background: "transparent", color: "var(--danger)", border: "1px solid var(--danger)", borderRadius: 7, padding: "5px 12px", fontFamily: "inherit", fontSize: 11.5, fontWeight: 600, cursor: "pointer" }}>Cancelar / descartar</button>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
