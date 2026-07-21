import { rootStyle } from "./theme";
import { EstelaProvider, useEstela } from "./store";
import Sidebar from "./Sidebar";
import { Toast, ZoomModal } from "./Chrome";
import InicioView from "./views/InicioView";
import DescubrimientosView from "./views/DescubrimientosView";
import ArbolView from "./views/ArbolView";
import PersonaView from "./views/PersonaView";
import BibliotecaView from "./views/BibliotecaView";
import VisorView from "./views/VisorView";
import BuscarView from "./views/BuscarView";
import SuperView from "./views/SuperView";
import FsView from "./views/FsView";
import AjustesView from "./views/AjustesView";
import LugaresView from "./views/LugaresView";
import HistorialView from "./views/HistorialView";

const KEYFRAMES = `
@keyframes estIn{from{transform:translateY(9px)}to{transform:none}}
@keyframes estPop{0%{transform:translateY(7px)}100%{transform:none}}
@keyframes estToast{from{opacity:0;transform:translate(-50%,16px)}to{opacity:1;transform:translate(-50%,0)}}
@keyframes estPulse{0%,100%{opacity:.5}50%{opacity:1}}
.estela-main::-webkit-scrollbar{width:9px;height:9px}
.estela-main::-webkit-scrollbar-thumb{background:var(--line);border-radius:8px}
.estela-root ::selection{background:rgba(217,83,30,.22)}
`;

export interface Account {
  email: string;
  onLogout: () => void;
}

function Shell({ account }: { account?: Account }) {
  const e = useEstela();
  return (
    <div className="estela-root" style={rootStyle(e.theme)}>
      <Sidebar account={account} />
      <main className="estela-main" style={{ flex: 1, minWidth: 0, height: "100vh", overflowY: "auto" }}>
        {e.nav === "inicio" && <InicioView />}
        {e.nav === "descubrimientos" && <DescubrimientosView />}
        {e.nav === "arbol" && <ArbolView />}
        {e.nav === "persona" && <PersonaView />}
        {e.nav === "biblioteca" && <BibliotecaView />}
        {e.nav === "visor" && <VisorView />}
        {e.nav === "buscar" && <BuscarView />}
        {e.nav === "super" && <SuperView />}
        {e.nav === "familysearch" && <FsView />}
        {e.nav === "ajustes" && <AjustesView />}
        {e.nav === "lugares" && <LugaresView />}
        {e.nav === "historial" && <HistorialView />}
      </main>
      <Toast />
      <ZoomModal />
    </div>
  );
}

export default function EstelaApp({ account }: { account?: Account }) {
  return (
    <EstelaProvider>
      <style>{KEYFRAMES}</style>
      <Shell account={account} />
    </EstelaProvider>
  );
}
