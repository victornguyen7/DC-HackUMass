import * as THREE from "three";
import { FOOTPRINT, STRUCTURES, TABLES, type Point } from "./hallGeometry";

const material = (color: string, roughness = .8, metalness = 0) => new THREE.MeshStandardMaterial({ color, roughness, metalness });

/** Plan meters map to (x, height, -y). Geometry stays shared with 2D and routing. */
function extrude(points: Point[], height: number, materials: THREE.Material | THREE.Material[]) {
  const shape = new THREE.Shape(points.map(([x, y]) => new THREE.Vector2(x, y)));
  const geometry = new THREE.ExtrudeGeometry(shape, { depth: height, bevelEnabled: false, steps: 1 });
  geometry.rotateX(-Math.PI / 2);
  const mesh = new THREE.Mesh(geometry, materials);
  mesh.castShadow = mesh.receiveShadow = true;
  return mesh;
}

export function buildHallModel() {
  const model = new THREE.Group();
  const stone = material("#dce3da"), edge = material("#45616a");
  const counter = material("#6b9290"), countertop = material("#f0e9d7");
  const roomTop = material("#8aa1a2"), roomWall = material("#59757f");
  const steel = material("#b1c3c2", .42, .35), glass = material("#98b6bc", .3, .15);
  const floor = extrude(FOOTPRINT, .65, [stone, edge]);
  floor.position.y = -.65;
  model.add(floor);

  const boxGeometry = new THREE.BoxGeometry(1, 1, 1);
  const box = (x: number, y: number, z: number, w: number, d: number, h: number, mat: THREE.Material) => {
    const mesh = new THREE.Mesh(boxGeometry, mat);
    mesh.position.set(x, z + h / 2, -y); mesh.scale.set(w, h, d);
    mesh.castShadow = mesh.receiveShadow = true;
    model.add(mesh);
    return mesh;
  };

  for (const solid of STRUCTURES) {
    const { id, polygon, height, kind } = solid;
    const mats = kind === "room" ? [roomTop, roomWall] : kind === "wall" ? [steel, glass] : kind === "stairs" ? [steel, roomWall] : [countertop, counter];
    const mesh = extrude(polygon, height, mats);
    const stationIds = id === "center-island" ? ["grill", "soups", "tandoor"]
      : id === "desserts-breads" ? ["desserts"] : id === "mediterranean-end" ? ["mediterranean"]
      : id === "display" ? ["display", "carving"] : [id.replaceAll("-", "_")];
    mesh.userData.stationIds = kind === "counter" ? stationIds : [];
    model.add(mesh);
    if (kind === "counter") {
      const lip = extrude(polygon, .09, countertop);
      lip.position.y = height; lip.userData.stationIds = stationIds; model.add(lip);
    }
  }

  // Windows and roof seams give the back-of-house volumes a readable scale.
  for (let x = 3; x < 61; x += 2.6) {
    box(x, 34.42, .6, 2.35, .08, 1.65, glass);
    box(x, 34.42, 2.26, 2.4, .12, .06, steel);
  }
  for (let x = 12; x < 50; x += 6) box(x, 30.35, 2.505, .035, 8.05, .015, steel);
  for (let i = 0; i < 13; i++) box(40.3 + i * .56, 8.75, .705, .035, 6.65, .015, roomTop);

  // Serving trays sit on the existing counters; they don't add new obstacles.
  const greens = [material("#6d9b6b"), material("#a2b877"), material("#d1a05d")];
  const trays: [number, number, number][] = [];
  for (const [start, y, count] of [[13, 24.6, 10], [24.5, 25.75, 6], [33.2, 25.75, 7], [50.1, 21.5, 5]] as const) {
    for (let i = 0; i < count; i++) trays.push([start + i * .91, y, 0]);
  }
  for (let i = 0; i < 5; i++) trays.push([24.2 + i * .83, 21.73 - i * .14, -.17]);
  for (let i = 0; i < 6; i++) trays.push([33.45, 15.3 + i * .9, Math.PI / 2]);
  for (let i = 0; i < 5; i++) trays.push([46.2 + i, 25.55 - i * .18, -.18]);
  trays.forEach(([x, y, angle], i) => {
    const tray = box(x, y, 1.3, .73, .44, .065, steel);
    tray.rotation.y = angle;
    const food = box(x, y, 1.37, .59, .31, .018, greens[i % greens.length]);
    food.rotation.y = angle;
  });

  // Shared meshes keep hundreds of small furniture pieces inexpensive on tablets.
  const tops = new THREE.InstancedMesh(boxGeometry, material("#c8aa7f"), TABLES.length);
  const legs = new THREE.InstancedMesh(boxGeometry, material("#52656a", .6, .15), TABLES.length * 4);
  const seats = new THREE.InstancedMesh(boxGeometry, material("#74999a"), TABLES.length * 4);
  const backs = new THREE.InstancedMesh(boxGeometry, material("#74999a"), TABLES.length * 4);
  const dummy = new THREE.Object3D();
  const instance = (mesh: THREE.InstancedMesh, index: number, x: number, y: number, z: number, w: number, d: number, h: number) => {
    dummy.position.set(x, z, -y); dummy.scale.set(w, h, d); dummy.updateMatrix(); mesh.setMatrixAt(index, dummy.matrix);
  };
  TABLES.forEach(([x, y], i) => {
    instance(tops, i, x, y, .76, 1.3, .76, .12);
    for (let j = 0; j < 4; j++) {
      const dx = j % 2 ? .45 : -.45, dy = j < 2 ? .68 : -.68;
      instance(legs, i * 4 + j, x + dx, y + Math.sign(dy) * .26, .35, .07, .07, .7);
      instance(seats, i * 4 + j, x + dx, y + dy, .42, .42, .42, .1);
      instance(backs, i * 4 + j, x + dx, y + dy + Math.sign(dy) * .2, .68, .42, .065, .46);
    }
  });
  for (const mesh of [tops, legs, seats, backs]) { mesh.castShadow = mesh.receiveShadow = true; model.add(mesh); }
  return model;
}
