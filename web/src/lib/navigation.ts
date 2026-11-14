import { FOOTPRINT, OBSTACLES, type Point } from "./hallGeometry";

// Fixed floor geometry: a small cached A* grid, with clearance for a student's
// body. Paths never fall back to a straight line through an obstacle.
const STEP = .25, CLEARANCE = .17;
const COLS = 254, ROWS = 139, COUNT = COLS * ROWS;
const polygons = [FOOTPRINT, ...OBSTACLES.map((o) => o.polygon)];
const bounds = polygons.map((p) => [Math.min(...p.map(([x]) => x)), Math.min(...p.map(([, y]) => y)), Math.max(...p.map(([x]) => x)), Math.max(...p.map(([, y]) => y))]);
const distance = (a: Point, b: Point) => Math.hypot(a[0] - b[0], a[1] - b[1]);
function edgeDistance(p: Point, a: Point, b: Point) {
  const dx = b[0] - a[0], dy = b[1] - a[1];
  const f = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
  return Math.hypot(p[0] - a[0] - dx * f, p[1] - a[1] - dy * f);
}
export function insidePolygon([x, y]: Point, polygon: Point[]) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [a, b] = polygon[i], [c, d] = polygon[j];
    if ((b > y) !== (d > y) && x < (c - a) * (y - b) / (d - b) + a) inside = !inside;
  }
  return inside;
}
export function isWalkable(p: Point) {
  if (!insidePolygon(p, FOOTPRINT)) return false;
  for (let k = 0; k < polygons.length; k++) {
    const [x0, y0, x1, y1] = bounds[k];
    if (p[0] < x0 - CLEARANCE || p[0] > x1 + CLEARANCE || p[1] < y0 - CLEARANCE || p[1] > y1 + CLEARANCE) continue;
    const poly = polygons[k];
    if (k > 0 && insidePolygon(p, poly)) return false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) if (edgeDistance(p, poly[j], poly[i]) < CLEARANCE - 1e-7) return false;
  }
  return true;
}
export function clearSegment(a: Point, b: Point) {
  if (!insidePolygon(a, FOOTPRINT) || !insidePolygon(b, FOOTPRINT)) return false;
  const left = Math.min(a[0], b[0]) - CLEARANCE, right = Math.max(a[0], b[0]) + CLEARANCE;
  const bottom = Math.min(a[1], b[1]) - CLEARANCE, top = Math.max(a[1], b[1]) + CLEARANCE;
  for (let k = 0; k < polygons.length; k++) {
    const [x0, y0, x1, y1] = bounds[k];
    if (right < x0 || left > x1 || top < y0 || bottom > y1) continue;
    const poly = polygons[k];
    if (k > 0 && (insidePolygon(a, poly) || insidePolygon(b, poly))) return false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const c = poly[j], d = poly[i];
      const cross = (p: Point, q: Point, r: Point) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
      const intersects = Math.max(a[0], b[0]) >= Math.min(c[0], d[0]) && Math.max(c[0], d[0]) >= Math.min(a[0], b[0])
        && Math.max(a[1], b[1]) >= Math.min(c[1], d[1]) && Math.max(c[1], d[1]) >= Math.min(a[1], b[1])
        && cross(a, b, c) * cross(a, b, d) <= 0 && cross(c, d, a) * cross(c, d, b) <= 0;
      if (intersects || Math.min(edgeDistance(a, c, d), edgeDistance(b, c, d), edgeDistance(c, a, b), edgeDistance(d, a, b)) < CLEARANCE - 1e-7) return false;
    }
  }
  return true;
}
const pointOf = (id: number): Point => [(id % COLS + .5) * STEP, (Math.floor(id / COLS) + .5) * STEP];
let floor: Uint8Array | undefined;
function grid() { return floor ??= Uint8Array.from({ length: COUNT }, (_, id) => Number(isWalkable(pointOf(id)))); }
const snapCache = new Map<string, Point>();
const key = (p: Point) => `${p[0].toFixed(4)},${p[1].toFixed(4)}`;
function nearestNode(p: Point, visible = false) {
  const walkable = grid(), cx = Math.max(0, Math.min(COLS - 1, Math.floor(p[0] / STEP))), cy = Math.max(0, Math.min(ROWS - 1, Math.floor(p[1] / STEP)));
  for (let radius = 0; radius < Math.max(COLS, ROWS); radius++) {
    let best = -1, bestDistance = Infinity;
    for (let y = Math.max(0, cy - radius); y <= Math.min(ROWS - 1, cy + radius); y++) for (let x = Math.max(0, cx - radius); x <= Math.min(COLS - 1, cx + radius); x++) {
      if (radius && Math.abs(x - cx) !== radius && Math.abs(y - cy) !== radius) continue;
      const id = y * COLS + x, pt = pointOf(id), d = distance(p, pt);
      if (walkable[id] && d < bestDistance && (!visible || clearSegment(p, pt))) { best = id; bestDistance = d; }
    }
    if (best >= 0) return best;
  }
  throw new Error("No accessible floor position");
}
export function snapToFloor(p: Point): Point {
  const k = key(p), cached = snapCache.get(k);
  if (cached) return cached;
  const snapped = isWalkable(p) ? p : pointOf(nearestNode(p));
  snapCache.set(k, snapped); return snapped;
}

class MinHeap {
  values: { id: number; score: number }[] = [];
  push(id: number, score: number) {
    let i = this.values.length; this.values.push({ id, score });
    while (i > 0) { const p = (i - 1) >> 1; if (this.values[p].score <= score) break; this.values[i] = this.values[p]; i = p; }
    this.values[i] = { id, score };
  }
  pop() {
    const root = this.values[0], last = this.values.pop()!;
    if (this.values.length) {
      let i = 0;
      while (i * 2 + 1 < this.values.length) {
        let child = i * 2 + 1;
        if (child + 1 < this.values.length && this.values[child + 1].score < this.values[child].score) child++;
        if (this.values[child].score >= last.score) break;
        this.values[i] = this.values[child]; i = child;
      }
      this.values[i] = last;
    }
    return root.id;
  }
}
export type Route = { points: Point[]; length: number };
const cache = new Map<string, Route>();
const visited = new Uint32Array(COUNT), closed = new Uint32Array(COUNT), cost = new Float64Array(COUNT), parent = new Int32Array(COUNT);
let search = 0;
const directions = [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1]];
const edgeKnown = new Uint8Array(COUNT), edgeOpen = new Uint8Array(COUNT);
function makeRoute(points: Point[]): Route { return { points, length: points.slice(1).reduce((sum, p, i) => sum + distance(points[i], p), 0) }; }
export function walkingRoute(from: Point, to: Point): Route {
  const a = snapToFloor(from), b = snapToFloor(to), routeKey = `${key(a)}>${key(b)}`;
  const cached = cache.get(routeKey); if (cached) return cached;
  const reverse = cache.get(`${key(b)}>${key(a)}`); if (reverse) { const route = { points: [...reverse.points].reverse(), length: reverse.length }; cache.set(routeKey, route); return route; }
  let points: Point[];
  if (clearSegment(a, b)) points = [a, b];
  else {
    const walkable = grid(), start = nearestNode(a, true), end = nearestNode(b, true), heap = new MinHeap();
    search++; visited[start] = search; cost[start] = 0; parent[start] = -1; heap.push(start, 0);
    while (heap.values.length) {
      const id = heap.pop(); if (closed[id] === search) continue;
      closed[id] = search; if (id === end) break;
      const x = id % COLS, y = Math.floor(id / COLS);
      for (let direction = 0; direction < directions.length; direction++) {
        const [dx, dy] = directions[direction];
        const nx = x + dx, ny = y + dy, next = ny * COLS + nx;
        if (nx < 0 || nx >= COLS || ny < 0 || ny >= ROWS || !walkable[next] || closed[next] === search) continue;
        // A diagonal may not cut a corner between two blocked grid cells.
        if (dx && dy && (!walkable[y * COLS + nx] || !walkable[ny * COLS + x])) continue;
        const bit = 1 << direction;
        if (!(edgeKnown[id] & bit)) { edgeKnown[id] |= bit; if (clearSegment(pointOf(id), pointOf(next))) edgeOpen[id] |= bit; }
        if (!(edgeOpen[id] & bit)) continue;
        const nextCost = cost[id] + (dx && dy ? Math.SQRT2 : 1);
        if (visited[next] === search && cost[next] <= nextCost) continue;
        visited[next] = search; cost[next] = nextCost; parent[next] = id;
        heap.push(next, nextCost + Math.hypot(nx - end % COLS, ny - Math.floor(end / COLS)));
      }
    }
    if (closed[end] !== search) throw new Error(`No walkable route from ${key(a)} to ${key(b)}`);
    const path: Point[] = [b];
    for (let id = end; id !== -1; id = parent[id]) path.push(pointOf(id));
    path.push(a); path.reverse();
    points = [path[0]];
    for (let i = 0; i < path.length - 1;) {
      let j = path.length - 1;
      while (j > i + 1 && !clearSegment(path[i], path[j])) j--;
      points.push(path[j]); i = j;
    }
  }
  const route = makeRoute(points);
  if (cache.size > 24000) cache.clear();
  cache.set(routeKey, route); return route;
}
export function alongRoute(route: Route, fraction: number): Point {
  let remaining = Math.max(0, Math.min(1, fraction)) * route.length;
  for (let i = 1; i < route.points.length; i++) {
    const a = route.points[i - 1], b = route.points[i], length = distance(a, b);
    if (remaining <= length) { const f = length ? remaining / length : 0; return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]; }
    remaining -= length;
  }
  return route.points[route.points.length - 1];
}
