import type { TreeGraph, TreePerson } from "../../api/tree";
import { NODE_W, NODE_H } from "./layout";

export interface AncNode extends TreePerson { x: number; y: number; gen: number }
export interface AncLink { x1: number; y1: number; x2: number; y2: number }
export interface Pedigree { nodes: AncNode[]; links: AncLink[]; width: number; height: number }

/** Parents of a person from the graph (the family where they are a child). */
export function parentsOf(graph: TreeGraph, id: string): { father?: string; mother?: string } {
  const fam = graph.families.find((f) => f.child_ids.includes(id));
  return { father: fam?.husband_id ?? undefined, mother: fam?.wife_id ?? undefined };
}

/** Horizontal ancestor (pedigree) layout: focus at the left, each generation a column to the right;
 *  vertical space halves per generation (binary ahnentafel). */
export function computePedigree(graph: TreeGraph, focusId: string, maxGen: number): Pedigree {
  const pmap = new Map(graph.persons.map((p) => [p.id, p]));
  const ROW = NODE_H + 16;
  const COL = NODE_W + 70;
  const leaves = Math.pow(2, maxGen);
  const height = leaves * ROW;
  const nodes: AncNode[] = [];
  const links: AncLink[] = [];

  function place(id: string | undefined, gen: number, top: number, bottom: number, child?: AncNode) {
    if (!id || gen > maxGen) return;
    const p = pmap.get(id);
    if (!p) return;
    const node: AncNode = { ...p, gen, x: gen * COL, y: (top + bottom) / 2 };
    nodes.push(node);
    if (child) links.push({ x1: child.x + NODE_W, y1: child.y + NODE_H / 2, x2: node.x, y2: node.y + NODE_H / 2 });
    if (gen < maxGen) {
      const { father, mother } = parentsOf(graph, id);
      const mid = (top + bottom) / 2;
      place(father, gen + 1, top, mid, node);
      place(mother, gen + 1, mid, bottom, node);
    }
  }
  place(focusId, 0, 0, height, undefined);

  return { nodes, links, width: Math.max((maxGen + 1) * COL, 400), height: Math.max(height, 200) };
}
