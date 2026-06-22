import type { TreeGraph } from "../../api/tree";
import { parentsOf } from "./pedigree";

export interface FanSeg {
  id: string;
  given: string | null;
  surname: string | null;
  sex: string;
  gen: number;
  a0: number; a1: number; // radians
  r0: number; r1: number;
}
export interface Fan { segs: FanSeg[]; R: number; ringW: number; center: number }

const RING = 62;

/** Radial ancestor fan: focus at the centre, each generation a concentric ring; an ancestor's arc
 *  subdivides its child's arc. Full circle (starts at the top). */
export function computeFan(graph: TreeGraph, focusId: string, maxGen: number): Fan {
  const pmap = new Map(graph.persons.map((p) => [p.id, p]));
  const segs: FanSeg[] = [];
  const start = -Math.PI / 2;

  function rec(id: string | undefined, gen: number, a0: number, a1: number) {
    if (gen > maxGen) return;
    const p = id ? pmap.get(id) : undefined;
    if (p && id) {
      segs.push({ id, given: p.given, surname: p.surname, sex: p.sex, gen, a0, a1, r0: gen * RING, r1: (gen + 1) * RING });
    }
    if (gen < maxGen) {
      const par = id ? parentsOf(graph, id) : {};
      const mid = (a0 + a1) / 2;
      rec(par.father, gen + 1, a0, mid);
      rec(par.mother, gen + 1, mid, a1);
    }
  }
  rec(focusId, 0, start, start + 2 * Math.PI);

  const R = (maxGen + 1) * RING;
  return { segs, R, ringW: RING, center: R };
}

/** SVG path for an annular sector (ring slice) centred at (cx, cy). */
export function arcPath(cx: number, cy: number, r0: number, r1: number, a0: number, a1: number): string {
  const pt = (r: number, a: number) => `${(cx + r * Math.cos(a)).toFixed(2)} ${(cy + r * Math.sin(a)).toFixed(2)}`;
  const large = a1 - a0 > Math.PI ? 1 : 0;
  if (r0 <= 0.01) {
    // wedge to the centre
    return `M ${cx} ${cy} L ${pt(r1, a0)} A ${r1} ${r1} 0 ${large} 1 ${pt(r1, a1)} Z`;
  }
  return `M ${pt(r1, a0)} A ${r1} ${r1} 0 ${large} 1 ${pt(r1, a1)} L ${pt(r0, a1)} A ${r0} ${r0} 0 ${large} 0 ${pt(r0, a0)} Z`;
}
