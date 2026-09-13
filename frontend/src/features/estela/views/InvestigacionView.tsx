import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import { fonts } from "../theme";
import { field, ghostBtn, primaryBtn } from "../ui";
import {
  createProject, createSource, createTask, listProjects, listSources, listTasks,
  patchTask, type ResearchProject, type ResearchTask, type Source,
} from "../../../api/research";
import { useEstela } from "../store";

const card = { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 13 } as const;
const ResearchRunsPanel = lazy(() => import("./ResearchRunsPanel"));

export default function InvestigacionView() {
  const { notify, go, openDoc, reloadDiscoveries } = useEstela();
  const [sources, setSources] = useState<Source[]>([]);
  const [projects, setProjects] = useState<ResearchProject[]>([]);
  const [tasks, setTasks] = useState<ResearchTask[]>([]);
  const [sourceTitle, setSourceTitle] = useState("");
  const [sourceType, setSourceType] = useState("document");
  const [projectTitle, setProjectTitle] = useState("");
  const [projectQuestion, setProjectQuestion] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [taskProject, setTaskProject] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    void Promise.all([listSources(), listProjects(), listTasks()])
      .then(([nextSources, nextProjects, nextTasks]) => {
        setSources(nextSources); setProjects(nextProjects); setTasks(nextTasks);
      })
      .catch(() => notify("No se pudo cargar el espacio de investigación", "var(--danger)"));
  }, [notify]);
  useEffect(() => { load(); }, [load]);

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    try { await action(); notify(success, "var(--ok)"); load(); }
    catch (err) { notify((err as Error).message, "var(--danger)"); }
    finally { setBusy(false); }
  }

  return (
    <section style={{ padding: "32px 44px 64px", maxWidth: 1180 }}>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 34, margin: 0, letterSpacing: "-.02em" }}>Investigación</h1>
        <p style={{ color: "var(--muted)", fontSize: 14, margin: "6px 0 0", maxWidth: 700 }}>
          Trabaja con evidencia: conserva la fuente, formula una afirmación y convierte las dudas en tareas revisables antes de alterar el árbol.
        </p>
      </div>

      <Suspense fallback={<p>Cargando investigaciones…</p>}><ResearchRunsPanel onDocument={openDoc}
        onReview={() => { void reloadDiscoveries(); go("descubrimientos"); }} /></Suspense>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(290px,1fr))", gap: 16, alignItems: "start" }}>
        <div style={card}>
          <div style={{ padding: "15px 17px", borderBottom: "1px solid var(--line2)" }}>
            <div style={{ fontWeight: 700, fontSize: 15 }}>Fuentes</div>
            <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 2 }}>Archivo, libro, registro o entrevista.</div>
          </div>
          <form onSubmit={(ev) => {
            ev.preventDefault();
            if (sourceTitle.trim()) void run(async () => {
              await createSource({ title: sourceTitle.trim(), source_type: sourceType });
              setSourceTitle("");
            }, "Fuente añadida");
          }} style={{ padding: 14, display: "flex", gap: 7 }}>
            <input value={sourceTitle} onChange={(ev) => setSourceTitle(ev.target.value)} placeholder="Título de la fuente" style={{ ...field, minWidth: 0, flex: 1 }} />
            <select value={sourceType} onChange={(ev) => setSourceType(ev.target.value)} style={{ ...field, width: 102 }}>
              <option value="document">Documento</option><option value="census">Padrón</option><option value="book">Libro</option><option value="oral">Oral</option>
            </select>
            <button disabled={busy} style={primaryBtn}>Añadir</button>
          </form>
          <div style={{ borderTop: "1px solid var(--line2)" }}>
            {sources.slice(0, 8).map((source) => <div key={source.id} style={{ padding: "10px 16px", borderBottom: "1px solid var(--line2)" }}>
              <div style={{ fontSize: 13.5, fontWeight: 600 }}>{source.title}</div>
              {(source.documents ?? []).map((document) => <button key={document.id} style={{ ...ghostBtn, marginTop: 6, marginRight: 6 }} onClick={() => openDoc(document.id)}>Abrir {document.title}</button>)}
              <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 2 }}>{source.source_type}{source.repository_name ? ` · ${source.repository_name}` : ""}{source.call_number ? ` · ${source.call_number}` : ""}</div>
            </div>)}
            {sources.length === 0 && <Empty text="Aún no hay fuentes. Añade una antes de citar una conclusión." />}
          </div>
        </div>

        <div style={card}>
          <div style={{ padding: "15px 17px", borderBottom: "1px solid var(--line2)" }}>
            <div style={{ fontWeight: 700, fontSize: 15 }}>Proyectos</div>
            <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 2 }}>Agrupa hipótesis, fuentes y trabajo pendiente.</div>
          </div>
          <form onSubmit={(ev) => {
            ev.preventDefault();
            if (projectTitle.trim()) void run(async () => {
              await createProject({ title: projectTitle.trim(), question: projectQuestion.trim() || undefined }); setProjectTitle(""); setProjectQuestion("");
            }, "Proyecto creado");
          }} style={{ padding: 14, display: "grid", gridTemplateColumns: "1fr auto", gap: 7 }}>
            <input value={projectTitle} onChange={(ev) => setProjectTitle(ev.target.value)} placeholder="p. ej. Rama Balsera" style={{ ...field, minWidth: 0, flex: 1 }} />
            <button disabled={busy} style={primaryBtn}>Crear</button>
            <input value={projectQuestion} onChange={(ev) => setProjectQuestion(ev.target.value)} placeholder="Pregunta: ¿quiénes fueron sus padres?" style={{ ...field, gridColumn: "1 / -1" }} />
          </form>
          <div style={{ borderTop: "1px solid var(--line2)" }}>
            {projects.map((project) => <div key={project.id} style={{ padding: "10px 16px", borderBottom: "1px solid var(--line2)" }}>
              <div style={{ fontSize: 13.5, fontWeight: 600 }}>{project.title}</div>
              {project.question && <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 3, lineHeight: 1.35 }}>{project.question}</div>}
              <div style={{ fontFamily: fonts.mono, fontSize: 10.5, color: project.open_tasks ? "var(--warn)" : "var(--ok)", marginTop: 2 }}>{project.open_tasks} tarea{project.open_tasks === 1 ? "" : "s"} abierta{project.open_tasks === 1 ? "" : "s"}</div>
            </div>)}
            {projects.length === 0 && <Empty text="Crea un proyecto para no perder el hilo de una hipótesis." />}
          </div>
        </div>

        <div style={card}>
          <div style={{ padding: "15px 17px", borderBottom: "1px solid var(--line2)" }}>
            <div style={{ fontWeight: 700, fontSize: 15 }}>Tareas</div>
            <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 2 }}>Una cola de trabajo clara, no notas sueltas.</div>
          </div>
          <form onSubmit={(ev) => {
            ev.preventDefault();
            if (taskTitle.trim()) void run(async () => {
              await createTask({ title: taskTitle.trim(), project_id: taskProject || undefined, priority: "medium" }); setTaskTitle("");
            }, "Tarea creada");
          }} style={{ padding: 14, display: "grid", gridTemplateColumns: "1fr auto", gap: 7 }}>
            <input value={taskTitle} onChange={(ev) => setTaskTitle(ev.target.value)} placeholder="Qué hay que comprobar" style={{ ...field, minWidth: 0 }} />
            <button disabled={busy} style={primaryBtn}>Añadir</button>
            <select value={taskProject} onChange={(ev) => setTaskProject(ev.target.value)} style={{ ...field, gridColumn: "1 / -1" }}>
              <option value="">Sin proyecto</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}
            </select>
          </form>
          <div style={{ borderTop: "1px solid var(--line2)" }}>
            {tasks.slice(0, 10).map((task) => <div key={task.id} style={{ display: "flex", alignItems: "center", gap: 9, padding: "10px 16px", borderBottom: "1px solid var(--line2)" }}>
              <button onClick={() => void run(() => patchTask(task.id, { status: task.status === "done" ? "open" : "done" }), task.status === "done" ? "Tarea reabierta" : "Tarea completada")} style={{ ...ghostBtn, width: 26, height: 26, padding: 0, color: task.status === "done" ? "var(--ok)" : "var(--muted)" }}>{task.status === "done" ? "✓" : "○"}</button>
              <div style={{ flex: 1, minWidth: 0, fontSize: 13.5, textDecoration: task.status === "done" ? "line-through" : "none", color: task.status === "done" ? "var(--muted)" : "var(--fg)" }}>{task.title}</div>
              <span style={{ fontFamily: fonts.mono, fontSize: 10, color: task.priority === "high" ? "var(--danger)" : "var(--muted)" }}>{task.priority}</span>
            </div>)}
            {tasks.length === 0 && <Empty text="No hay tareas abiertas. Añade la siguiente comprobación concreta." />}
          </div>
        </div>
      </div>
    </section>
  );
}

function Empty({ text }: { text: string }) {
  return <div style={{ color: "var(--muted)", fontSize: 12.5, padding: "18px 16px", lineHeight: 1.45 }}>{text}</div>;
}
