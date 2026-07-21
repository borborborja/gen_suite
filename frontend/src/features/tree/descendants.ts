import type { TreeGraph, TreePerson } from "../../api/tree";
import { NODE_W, NODE_H } from "./layout";

// Descendant chart: mirror of pedigree.ts but walking DOWN — root on the left, one column
// per generation, children positioned post-order (a parent centers on its children).

export interface DescNode extends TreePerson {
  x: number;
  y: number;
  gen: number;
  spouses: string[]; // display names of spouses, shown as a secondary "⚭" label
}
export interface DescLink { x1: number; y1: number; x2: number; y2: number }
export interface Descendants { nodes: DescNode[]; links: DescLink[]; width: number; height: number }

const GAP_X = 70;
const ROW_H = NODE_H + 26; // extra room for the spouse label under the node

export function computeDescendants(graph: TreeGraph, rootId: string, maxGen: number): Descendants {
  const byId = new Map(graph.persons.map((p) => [p.id, p]));
  const nodes: DescNode[] = [];
  const links: DescLink[] = [];
  const visited = new Set<string>();
  let nextRow = 0;

  const spouseName = (id: string | null): string | null => {
    const p = id ? byId.get(id) : undefined;
    if (!p) return null;
    return [p.given, p.surname].filter(Boolean).join(" ") || null;
  };

  function childrenOf(id: string): { children: string[]; spouses: string[] } {
    const children: string[] = [];
    const spouses: string[] = [];
    for (const f of graph.families) {
      if (f.husband_id !== id && f.wife_id !== id) continue;
      const sp = spouseName(f.husband_id === id ? f.wife_id : f.husband_id);
      if (sp) spouses.push(sp);
      for (const c of f.child_ids) if (!children.includes(c)) children.push(c);
    }
    return { children, spouses };
  }

  function place(id: string, gen: number): DescNode | null {
    if (visited.has(id)) return null; // guard: cycles / duplicated lines
    visited.add(id);
    const person = byId.get(id);
    if (!person) return null;

    const { children, spouses } = childrenOf(id);
    const childNodes: DescNode[] = [];
    if (gen < maxGen) {
      for (const c of children) {
        const n = place(c, gen + 1);
        if (n) childNodes.push(n);
      }
    }
    const y = childNodes.length
      ? (Math.min(...childNodes.map((c) => c.y)) + Math.max(...childNodes.map((c) => c.y))) / 2
      : nextRow++ * ROW_H;
    const node: DescNode = { ...person, x: gen * (NODE_W + GAP_X), y, gen, spouses };
    nodes.push(node);
    for (const c of childNodes) {
      links.push({ x1: node.x + NODE_W, y1: node.y + NODE_H / 2, x2: c.x, y2: c.y + NODE_H / 2 });
    }
    return node;
  }

  place(rootId, 0);
  const width = (Math.max(0, ...nodes.map((n) => n.gen)) + 1) * (NODE_W + GAP_X);
  const height = Math.max(1, nextRow) * ROW_H;
  return { nodes, links, width, height };
}
