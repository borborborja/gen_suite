import type { TreeGraph, TreePerson } from "../../api/tree";

export const NODE_W = 168;
export const NODE_H = 60;
const GAP_X = 28;
const GAP_Y = 96;

export interface PositionedPerson extends TreePerson {
  x: number;
  y: number;
  level: number;
}
export interface Link {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}
export interface Layout {
  nodes: PositionedPerson[];
  parentLinks: Link[];
  coupleLinks: Link[];
  width: number;
  height: number;
}

/**
 * Layered layout: BFS generation levels from the focus across family edges (parents above,
 * children below, spouses on the same level), then per-level horizontal stacking centered.
 * Family connectors use a midpoint bar. Good for the depth-limited ego subgraphs we fetch.
 */
export function computeLayout(graph: TreeGraph): Layout {
  const level = new Map<string, number>();
  level.set(graph.focus, 0);

  let changed = true;
  let guard = 0;
  while (changed && guard++ < 200) {
    changed = false;
    for (const f of graph.families) {
      const parents = [f.husband_id, f.wife_id].filter(Boolean) as string[];
      const knownParent = parents.find((p) => level.has(p));
      if (knownParent !== undefined) {
        const L = level.get(knownParent)!;
        for (const p of parents)
          if (!level.has(p)) { level.set(p, L); changed = true; }
        for (const c of f.child_ids)
          if (!level.has(c)) { level.set(c, L + 1); changed = true; }
      }
      const knownChild = f.child_ids.find((c) => level.has(c));
      if (knownChild !== undefined) {
        const L = level.get(knownChild)!;
        for (const p of parents)
          if (!level.has(p)) { level.set(p, L - 1); changed = true; }
        for (const c of f.child_ids)
          if (!level.has(c)) { level.set(c, L); changed = true; }
      }
    }
  }
  for (const p of graph.persons) if (!level.has(p.id)) level.set(p.id, 0);

  const byLevel = new Map<number, string[]>();
  for (const p of graph.persons) {
    const L = level.get(p.id)!;
    if (!byLevel.has(L)) byLevel.set(L, []);
    byLevel.get(L)!.push(p.id);
  }
  const levels = [...byLevel.keys()].sort((a, b) => a - b);
  const maxRow = Math.max(1, ...levels.map((L) => byLevel.get(L)!.length));
  const width = maxRow * (NODE_W + GAP_X);

  const pos = new Map<string, { x: number; y: number }>();
  levels.forEach((L, li) => {
    const ids = byLevel.get(L)!;
    const rowW = ids.length * (NODE_W + GAP_X);
    const offset = (width - rowW) / 2;
    ids.forEach((id, i) => {
      pos.set(id, { x: offset + i * (NODE_W + GAP_X), y: li * (NODE_H + GAP_Y) });
    });
  });

  const nodes: PositionedPerson[] = graph.persons.map((p) => ({
    ...p,
    level: level.get(p.id)!,
    x: pos.get(p.id)!.x,
    y: pos.get(p.id)!.y,
  }));

  const parentLinks: Link[] = [];
  const coupleLinks: Link[] = [];
  for (const f of graph.families) {
    const parents = ([f.husband_id, f.wife_id].filter(Boolean) as string[])
      .map((id) => pos.get(id))
      .filter(Boolean) as { x: number; y: number }[];
    let midX = 0;
    let midY = 0;
    if (parents.length === 2) {
      coupleLinks.push({
        x1: parents[0].x + NODE_W / 2,
        y1: parents[0].y + NODE_H / 2,
        x2: parents[1].x + NODE_W / 2,
        y2: parents[1].y + NODE_H / 2,
      });
      midX = (parents[0].x + parents[1].x) / 2 + NODE_W / 2;
      midY = Math.max(parents[0].y, parents[1].y) + NODE_H;
    } else if (parents.length === 1) {
      midX = parents[0].x + NODE_W / 2;
      midY = parents[0].y + NODE_H;
    } else {
      continue;
    }
    for (const cid of f.child_ids) {
      const cp = pos.get(cid);
      if (!cp) continue;
      parentLinks.push({ x1: midX, y1: midY, x2: cp.x + NODE_W / 2, y2: cp.y });
    }
  }

  return {
    nodes,
    parentLinks,
    coupleLinks,
    width: Math.max(width, 400),
    height: Math.max(levels.length * (NODE_H + GAP_Y), 200),
  };
}
