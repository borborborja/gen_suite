# frontend — guía para agentes

React 18 + Vite + TypeScript estricto. Sin router ni librería de estado: la navegación y el
estado global viven en el store propio de `src/features/estela/store.tsx`.

## Comandos

```bash
npm install
npm run dev        # :5173, proxy /api → localhost:8000 (en prod lo hace nginx)
npm run typecheck  # tsc --noEmit — ejecútalo tras cada cambio (no hay linter ni tests)
npm run build
```

## Estructura

- `src/api/` — un cliente fetch por módulo del backend; tipos compartidos en `types.ts`.
  Para llamadas nuevas usa `api()` / `authFetch()` de `client.ts` (gestionan el Bearer token
  y el refresh single-flight en 401) — nunca `fetch` a pelo ni headers de auth a mano.
  Las rutas se pasan sin el prefijo `/api` (el cliente lo añade).
- `src/features/estela/` — la aplicación (chrome, sidebar, store, tema); las pantallas en
  `views/*.tsx` (`BibliotecaView`, `ArbolView`, `BuscarView`…).
- `src/features/tree/` — algoritmos de layout del árbol (pedigree/fan/generaciones, d3-zoom).

## Convenciones

- **No añadir dependencias sin preguntar.** La superficie actual es mínima a propósito:
  react, react-dom, d3-selection, d3-zoom. Nada de UI kits, routers ni state managers.
- Strings visibles para el usuario en **español**; nombres de vistas también (`AjustesView`).
- Endpoint nuevo en el backend → añade el tipo en `src/api/types.ts` y la función en el
  cliente `src/api/<módulo>.ts` correspondiente; las vistas no llaman a `fetch` directamente.
- SSE de jobs y binarios (imágenes/PDF) van con `authFetch` (necesitan el token).
- Estilos: CSS propio (`index.css` + `theme.ts`/`ui.tsx` de estela); imita lo existente.
