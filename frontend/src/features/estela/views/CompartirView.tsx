import { useCallback, useEffect, useState } from "react";
import { fonts } from "../theme";
import { field, ghostBtn, primaryBtn } from "../ui";
import {
  createPublication, listProposals, listPublications, publishPublication, revokePublication,
  refreshPublication, reviewProposal, type Proposal, type Publication,
} from "../../../api/collaboration";
import { listPersons, type PersonRow } from "../../../api/tree";
import { useEstela } from "../store";

const card = { background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 13 } as const;

function label(person: PersonRow) { return [person.given, person.surname].filter(Boolean).join(" ") || "(sin nombre)"; }

export default function CompartirView() {
  const { notify } = useEstela();
  const [people, setPeople] = useState<PersonRow[]>([]);
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [publications, setPublications] = useState<Publication[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [title, setTitle] = useState("");
  const [secretLink, setSecretLink] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    void Promise.all([listPersons({ page_size: 200 }), listProposals("pending"), listPublications()])
      .then(([directory, nextProposals, nextPublications]) => {
        setPeople(directory.items.filter((person) => person.privacy_status === "deceased" || person.privacy_status === "public"));
        setProposals(nextProposals); setPublications(nextPublications);
      })
      .catch(() => notify("No se pudo cargar la colaboración", "var(--danger)"));
  }, [notify]);
  useEffect(() => { load(); }, [load]);

  async function run(action: () => Promise<void>, success: string) {
    setBusy(true);
    try { await action(); notify(success, "var(--ok)"); load(); }
    catch (err) { notify((err as Error).message, "var(--danger)"); }
    finally { setBusy(false); }
  }
  function toggle(id: string) {
    setSelected((old) => { const next = new Set(old); next.has(id) ? next.delete(id) : next.add(id); return next; });
  }

  return (
    <section style={{ padding: "32px 44px 64px", maxWidth: 1100 }}>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontFamily: fonts.serif, fontWeight: 600, fontSize: 34, margin: 0, letterSpacing: "-.02em" }}>Compartir y revisar</h1>
        <p style={{ color: "var(--muted)", fontSize: 14, margin: "6px 0 0", maxWidth: 720 }}>
          Las propuestas quedan separadas del árbol aprobado. Los enlaces son no listados, revocables y solo incluyen personas revisadas para publicación.
        </p>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.3fr) minmax(300px,.7fr)", gap: 16, alignItems: "start" }}>
        <div style={card}>
          <div style={{ padding: "15px 17px", borderBottom: "1px solid var(--line2)" }}>
            <div style={{ fontWeight: 700, fontSize: 15 }}>Árbol compartido no listado</div>
            <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 2 }}>Selecciona solo antepasados históricos marcados como publicables.</div>
          </div>
          <div style={{ padding: 15, display: "flex", gap: 8 }}>
            <input value={title} onChange={(ev) => setTitle(ev.target.value)} placeholder="Título del enlace" style={{ ...field, flex: 1, minWidth: 0 }} />
            <button disabled={busy || !title.trim() || selected.size === 0} onClick={() => void run(async () => {
              const created = await createPublication({ title: title.trim(), person_ids: [...selected] });
              // Fragments never reach nginx, application logs, Referer headers or browser
              // history requests. The public page submits the token in its POST body instead.
              const link = `${window.location.origin}${created.share_path}#${created.access_token}`;
              setSecretLink(link); setTitle(""); setSelected(new Set());
            }, "Publicación preparada: compártela solo con quien corresponda") } style={primaryBtn}>Crear enlace</button>
          </div>
          {secretLink && <div style={{ margin: "0 15px 14px", padding: 12, borderRadius: 9, background: "var(--ok-faint)", color: "var(--ok)", fontSize: 12.5 }}>
            <div style={{ fontWeight: 700, marginBottom: 5 }}>Copia ahora el enlace secreto: el token no vuelve a mostrarse.</div>
            <code style={{ display: "block", overflowWrap: "anywhere", color: "var(--fg)", fontFamily: fonts.mono }}>{secretLink}</code>
            <button onClick={() => void navigator.clipboard?.writeText(secretLink)} style={{ ...ghostBtn, marginTop: 8 }}>Copiar enlace</button>
          </div>}
          <div style={{ borderTop: "1px solid var(--line2)", maxHeight: 290, overflowY: "auto" }}>
            {people.map((person) => <label key={person.id} style={{ display: "flex", gap: 10, alignItems: "center", padding: "10px 16px", borderBottom: "1px solid var(--line2)", cursor: "pointer" }}>
              <input type="checkbox" checked={selected.has(person.id)} onChange={() => toggle(person.id)} />
              <span style={{ flex: 1, fontSize: 13.5, fontWeight: 600 }}>{label(person)}</span>
              <span style={{ fontFamily: fonts.mono, fontSize: 10, color: "var(--ok)" }}>{person.privacy_status === "deceased" ? "HISTÓRICA" : "PÚBLICA"}</span>
            </label>)}
            {people.length === 0 && <div style={{ color: "var(--muted)", fontSize: 12.5, padding: 18 }}>No hay personas publicables. En la ficha de una persona, marca su visibilidad como “Histórica, fallecida” tras revisarla.</div>}
          </div>
          <div style={{ padding: 12, borderTop: "1px solid var(--line2)" }}>
            {publications.map((publication) => <div key={publication.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 4px", fontSize: 12.5 }}>
              <span style={{ flex: 1, fontWeight: 600 }}>{publication.title}</span>
              <span style={{ fontFamily: fonts.mono, fontSize: 10, color: publication.status === "published" ? "var(--ok)" : "var(--muted)" }}>{publication.status}</span>
              {publication.status !== "revoked" && <button style={ghostBtn} onClick={() => void run(() => refreshPublication(publication.id).then(() => undefined), "Instantánea actualizada y revisada")}>Actualizar</button>}
              {publication.status === "draft" && <button style={ghostBtn} onClick={() => void run(() => publishPublication(publication.id).then(() => undefined), "Publicación activada")}>Publicar</button>}
              {publication.status !== "revoked" && <button style={{ ...ghostBtn, color: "var(--danger)" }} onClick={() => void run(() => revokePublication(publication.id).then(() => undefined), "Enlace revocado")}>Revocar</button>}
            </div>)}
          </div>
        </div>

        <div style={card}>
          <div style={{ padding: "15px 17px", borderBottom: "1px solid var(--line2)" }}>
            <div style={{ fontWeight: 700, fontSize: 15 }}>Propuestas pendientes</div>
            <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 2 }}>Un editor puede aceptar o rechazar cada cambio.</div>
          </div>
          {proposals.map((proposal) => <div key={proposal.id} style={{ padding: 14, borderBottom: "1px solid var(--line2)" }}>
            <div style={{ fontSize: 13, fontWeight: 700 }}>{proposal.operation}</div>
            {proposal.rationale && <div style={{ fontSize: 12.5, color: "var(--muted)", marginTop: 4 }}>{proposal.rationale}</div>}
            <pre style={{ margin: "8px 0", padding: 8, overflowX: "auto", borderRadius: 6, background: "var(--bg)", fontSize: 10.5, color: "var(--muted)" }}>{JSON.stringify(proposal.patch, null, 2)}</pre>
            <div style={{ display: "flex", gap: 7 }}>
              <button style={ghostBtn} onClick={() => void run(() => reviewProposal(proposal.id, "rejected").then(() => undefined), "Propuesta rechazada")}>Rechazar</button>
              <button style={primaryBtn} onClick={() => void run(() => reviewProposal(proposal.id, "accepted").then(() => undefined), "Propuesta aceptada; aplícala con la evidencia revisada")}>Aceptar</button>
            </div>
          </div>)}
          {proposals.length === 0 && <div style={{ color: "var(--muted)", fontSize: 12.5, padding: 18 }}>No hay propuestas pendientes.</div>}
        </div>
      </div>
    </section>
  );
}
