import zones from "./zones.json";

export type Point = [number, number];
export type Solid = { id: string; polygon: Point[]; height: number; kind: "counter" | "room" | "wall" | "stairs" | "table" };
const rect = (x: number, y: number, w: number, h: number): Point[] => [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
const solid = (id: string, polygon: Point[], height = 1.2, kind: Solid["kind"] = "counter"): Solid => ({ id, polygon, height, kind });

// Meter coordinates traced from the plan. The same solids drive navigation and
// the illustrated view. Counter interiors and back-of-house rooms are not aisles.
export const STRUCTURES: Solid[] = [
  solid("west-rooms", rect(1.6, 24.9, 8.3, 9.6), 2.5, "room"),
  solid("kitchen", rect(9.9, 26.3, 41.7, 8.2), 2.5, "room"),
  solid("east-rooms", rect(51.6, 26, 10.3, 8.5), 2.5, "room"),
  solid("dish-return", rect(10, 25.6, 1.9, .7)),
  solid("desserts-breads", rect(12.4, 24.2, 10.8, .8)),
  solid("mediterranean", rect(24, 25.2, 6, 1.1)),
  solid("mediterranean-end", Array.from({ length: 16 }, (_, i): Point => [30.7 + .8 * Math.cos(i * Math.PI / 8), 25.7 + .8 * Math.sin(i * Math.PI / 8)])),
  solid("seasons", rect(32.6, 25.2, 7.2, 1.1)),
  solid("wok", rect(40, 25.2, 5.4, 1.1)),
  solid("street-food", [[45.6, 25.6], [51.2, 24.6], [51.2, 25.4], [45.6, 26.4]]),
  solid("latino", [[4.3, 13.4], [11, 13.4], ...Array.from({ length: 13 }, (_, i): Point => {
    const a = -Math.PI / 2 + i * Math.PI / 12;
    return [11 + 3.85 * Math.cos(a), 17.5 + 4.1 * Math.sin(a)];
  }), [4.3, 21.6]]),
  solid("center-island", [[23.4, 22.1], [28.7, 21.2], [33.9, 22.1], [33.9, 14.4], [28.7, 13.6], [23.4, 14.4]]),
  solid("sushi", rect(47.4, 15, 4.1, 7)),
  solid("noodle-bowl", rect(49.6, 21, 5.8, 1)),
  solid("display", Array.from({ length: 24 }, (_, i): Point => [55.4 + 2.85 * Math.cos(i * Math.PI / 12), 15.1 + 2.85 * Math.sin(i * Math.PI / 12)])),
  solid("stairs", rect(38.9, 5.2, 10.3, 7.1), .7, "stairs"),
  solid("entry-base", rect(36.6, 5.3, 2.3, 2.1), 1),
  solid("check-in-west", rect(36.3, 8.6, 1, 2.6), 1),
  solid("check-in-east", rect(37.9, 8.6, .7, 2.6), 1),
  // Three openings in the back-room partition: x=32–33.4, 56.2–57.4, 60.8–62.
  ...[[30.8, 32], [33.4, 38.9], [49.2, 56.2], [57.4, 60.8], [62, 63.5]].map(([a, b], i) => solid(`partition-${i}`, rect(a, 6.4, b - a, .2), 1.5, "wall")),
];
export const FOOTPRINT = zones.footprint_m as Point[];
// Give the first row enough room for a chair between the table and south wall.
export const SEATING = Object.fromEntries(Object.entries(zones.zones).map(([id, zone]) => [id,
  zone.table_xy_m.map(([x, y]): Point => [x, id === "main" && y === 7.1 ? 7.45 : y]),
])) as Record<string, Point[]>;
export const TABLES = Object.values(SEATING).flat();
export const TABLE_SOLIDS = TABLES.map(([x, y], i) => solid(`table-${i}`, rect(x - .65, y - .38, 1.3, .76), .72, "table"));
export const OBSTACLES = [...STRUCTURES, ...TABLE_SOLIDS];
