import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { buildHallModel } from "./hallSceneModel";
import { createTapTracker, layoutStationLabels, type LabelPosition } from "./hallSceneInteraction";
import { FOOTPRINT } from "./hallGeometry";
import { stationName } from "./format";
import type { Pose } from "./playback";

export type SceneStation = { id: string; pos: [number, number]; line: number; empty: number };
export type SceneData = {
  poses: Pose[]; stations: SceneStation[]; selectedStation: string | null; selectedStudent: number | null;
  colorBy: "diet" | "wait" | "missed"; missed: Set<number>;
};
export type SceneCallbacks = {
  onStation: (id: string) => void; onStudent: (id: number) => void;
  onLabels: (labels: LabelPosition[]) => void; onUnavailable: () => void;
};
export type HallSceneController = ReturnType<typeof createHallScene>;
const CENTER = new THREE.Vector3(31.75, 0, -17.25);
const OFFSET = new THREE.Vector3(24, 65, 100);
const DIET: Record<string, string> = { none: "#88afe2", vegetarian: "#a4d59c", vegan: "#5bc5b0", halal: "#eac28d" };

/** Fit all plan corners, at their full height, with space for controls and labels. */
export function fitHallCamera(camera: THREE.OrthographicCamera, width: number, height: number) {
  camera.updateMatrixWorld();
  const bounds = new THREE.Box3();
  for (const [x, y] of FOOTPRINT) {
    for (const h of [-.65, 3]) bounds.expandByPoint(new THREE.Vector3(x, h, -y).applyMatrix4(camera.matrixWorldInverse));
  }
  const aspect = Math.max(1, width) / Math.max(1, height);
  const usableHeight = Math.max(100, height - 86);
  const unitsPerPixel = Math.max((bounds.max.y - bounds.min.y) / (usableHeight * .92), (bounds.max.x - bounds.min.x) / (width * .9));
  const half = unitsPerPixel * height / 2;
  const verticalOffset = 43 * unitsPerPixel;
  camera.left = -half * aspect; camera.right = half * aspect;
  camera.top = half - verticalOffset; camera.bottom = -half - verticalOffset;
  camera.updateProjectionMatrix();
}

export function createHallScene(canvas: HTMLCanvasElement, callbacks: SceneCallbacks) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setClearColor(0x000000, 0);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-50, 50, 30, -30, .1, 300);
  camera.position.copy(CENTER).add(OFFSET); camera.lookAt(CENTER);
  const controls = new OrbitControls(camera, canvas);
  controls.target.copy(CENTER); controls.cursor.copy(CENTER);
  controls.maxTargetRadius = 40;
  controls.minPolarAngle = .12; controls.maxPolarAngle = Math.PI / 2.25;
  controls.minZoom = .65; controls.maxZoom = 5;
  controls.rotateSpeed = .45; controls.panSpeed = .75; controls.zoomSpeed = .8;
  controls.screenSpacePanning = false;
  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  controls.enableDamping = !motion.matches; controls.dampingFactor = .12;
  controls.update();
  const model = buildHallModel(); scene.add(model);
  scene.add(new THREE.HemisphereLight("#ecf5ff", "#526e6e", 1.65));
  const sun = new THREE.DirectionalLight("#fff2dd", 2.5);
  sun.position.set(12, 65, 20); sun.target.position.copy(CENTER);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -46, right: 46, top: 46, bottom: -46, near: 1, far: 145 });
  sun.shadow.normalBias = .06; sun.shadow.bias = -.00035;
  scene.add(sun, sun.target);
  const fill = new THREE.DirectionalLight("#b9dbe8", .65); fill.position.set(70, 20, -50); scene.add(fill);

  const selection = new THREE.Mesh(new THREE.RingGeometry(.38, .51, 40), new THREE.MeshBasicMaterial({ color: "#f6b5c3", side: THREE.DoubleSide }));
  selection.rotation.x = -Math.PI / 2; selection.visible = false; scene.add(selection);
  const stationRing = new THREE.Mesh(new THREE.RingGeometry(.7, .78, 48), new THREE.MeshBasicMaterial({ color: "#f5d79e", side: THREE.DoubleSide, depthTest: false }));
  stationRing.rotation.x = -Math.PI / 2; stationRing.renderOrder = 2; stationRing.visible = false; scene.add(stationRing);

  let data: SceneData = { poses: [], stations: [], selectedStudent: null, selectedStation: null, colorBy: "wait", missed: new Set() };
  let width = 1, height = 1, frame = 0, disposed = false, lost = false, labelsDirty = true, stationKey = "";
  let capacity = 0, people: THREE.InstancedMesh[] = [];
  let previous = new Map<number, { x: number; y: number; angle: number }>();
  const dummy = new THREE.Object3D(), color = new THREE.Color();
  const raycaster = new THREE.Raycaster(), pointer = new THREE.Vector2(), point = new THREE.Vector3();
  const taps = createTapTracker();

  function invalidate() {
    if (!frame && !disposed && !lost && !document.hidden) frame = requestAnimationFrame(draw);
  }
  function positionLabels() {
    const anchors = data.stations.filter((s) => s.id !== "breads").map((s) => {
      point.set(s.pos[0], 1.7, -s.pos[1]).project(camera);
      return { id: s.id, label: s.id === "desserts" ? "Desserts · Breads" : stationName(s.id), x: (point.x + 1) * width / 2, y: (1 - point.y) * height / 2, visible: point.z > -1 && point.z < 1 };
    });
    callbacks.onLabels(layoutStationLabels(anchors.sort((a, b) => a.y - b.y || a.x - b.x), width, height));
  }
  function draw() {
    frame = 0;
    if (disposed || lost || document.hidden) return;
    const changing = controls.update();
    renderer.render(scene, camera);
    if (labelsDirty) { positionLabels(); labelsDirty = false; }
    if (changing) invalidate();
  }
  function cameraChanged() { labelsDirty = true; invalidate(); }
  controls.addEventListener("change", cameraChanged);

  function disposePeople() {
    for (const mesh of people) { scene.remove(mesh); mesh.geometry.dispose(); (mesh.material as THREE.Material).dispose(); mesh.dispose(); }
    people = [];
  }
  function allocatePeople(count: number) {
    if (capacity >= count && people.length) return;
    disposePeople();
    capacity = Math.max(512, 2 ** Math.ceil(Math.log2(Math.max(1, count))));
    const parts: [THREE.BufferGeometry, string][] = [
      [new THREE.CapsuleGeometry(.17, .43, 3, 8), "#ffffff"],
      [new THREE.SphereGeometry(.15, 10, 8), "#e6ccb0"],
      [new THREE.CylinderGeometry(.065, .065, .54, 6), "#405766"],
      [new THREE.CylinderGeometry(.065, .065, .54, 6), "#405766"],
      [new THREE.CapsuleGeometry(.055, .32, 2, 6), "#ffffff"],
      [new THREE.CapsuleGeometry(.055, .32, 2, 6), "#ffffff"],
      [new THREE.CylinderGeometry(.17, .14, .045, 12), "#ffffff"],
    ];
    people = parts.map(([geometry, tint], i) => {
      const mesh = new THREE.InstancedMesh(geometry, new THREE.MeshStandardMaterial({ color: tint, roughness: .9 }), capacity);
      mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
      // Fixed bounds include every navigable point; updated poses cannot be culled using old bounds.
      mesh.boundingSphere = new THREE.Sphere(CENTER, 80);
      mesh.castShadow = i < 2; mesh.receiveShadow = true; mesh.count = 0;
      mesh.userData.person = true;
      scene.add(mesh); return mesh;
    });
  }
  function update(next: SceneData) {
    if (disposed || lost) return;
    data = next;
    const key = data.stations.map((s) => `${s.id}:${s.pos.join(",")}`).join(";");
    if (key !== stationKey) { stationKey = key; labelsDirty = true; }
    allocatePeople(data.poses.length);
    people.forEach((mesh) => { mesh.count = data.poses.length; });
    const nextPrevious = new Map<number, { x: number; y: number; angle: number }>();
    selection.visible = false;
    data.poses.forEach((p, index) => {
      const [x, y] = p.xy, old = previous.get(p.id);
      const moving = p.phase === "walking" || p.phase === "returning";
      const angle = moving && old && Math.hypot(x - old.x, y - old.y) > .002 ? Math.atan2(x - old.x, -(y - old.y)) : old?.angle ?? (p.id % 4) * Math.PI / 2;
      nextPrevious.set(p.id, { x, y, angle });
      const seated = p.phase === "seated", bodyY = seated ? .77 : 1.03;
      const step = moving ? Math.sin((x + y) * 6 + p.id) * .3 : 0;
      const tint = data.colorBy === "diet" ? DIET[p.diet] ?? "#d7e4ea"
        : data.colorBy === "missed" ? data.missed.has(p.id) ? "#e58982" : "#bdced2"
        : p.phase === "line" ? (p.waitedMin ?? 0) > 10 ? "#e58982" : (p.waitedMin ?? 0) > 5 ? "#e7b976" : "#8fc9ad"
        : seated ? "#94b7bd" : "#d9e8e8";
      const part = (i: number, dx: number, dy: number, dz: number, tilt = 0, scale = 1) => {
        dummy.position.set(x + dx * Math.cos(angle) + dz * Math.sin(angle), dy, -y - dx * Math.sin(angle) + dz * Math.cos(angle));
        dummy.rotation.set(tilt, angle, 0); dummy.scale.setScalar(scale); dummy.updateMatrix(); people[i].setMatrixAt(index, dummy.matrix);
      };
      part(0, 0, bodyY, 0); part(1, 0, bodyY + .5, 0);
      part(2, -.1, seated ? .4 : .32, seated ? .16 : 0, seated ? -1.15 : step);
      part(3, .1, seated ? .4 : .32, seated ? .16 : 0, seated ? -1.15 : -step);
      part(4, -.23, bodyY - .03, .04, -step); part(5, .23, bodyY - .03, .04, step);
      part(6, .24, bodyY - .1, .25, 0, p.hands === "empty" ? 0 : 1);
      for (const i of [0, 4, 5]) people[i].setColorAt(index, color.set(tint));
      people[6].setColorAt(index, color.set(p.hands === "bowl" ? "#deb57b" : "#ffffff"));
      if (p.id === data.selectedStudent) { selection.visible = true; selection.position.set(x, .035, -y); }
    });
    previous = nextPrevious;
    people.forEach((mesh) => { mesh.instanceMatrix.needsUpdate = true; if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true; });
    const selected = data.stations.find((s) => s.id === data.selectedStation);
    stationRing.visible = !!selected;
    if (selected) stationRing.position.set(selected.pos[0], 1.35, -selected.pos[1]);
    invalidate();
  }

  function resize() {
    const rect = canvas.parentElement!.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return;
    width = rect.width; height = rect.height;
    // Retina sharpness without allocating an unbounded drawing buffer on big displays.
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2.5, Math.sqrt(4_000_000 / (width * height))));
    renderer.setSize(width, height, false);
    fitHallCamera(camera, width, height); labelsDirty = true; invalidate();
  }
  const observer = new ResizeObserver(resize); observer.observe(canvas.parentElement!);

  function zoom(factor: number) {
    camera.zoom = THREE.MathUtils.clamp(camera.zoom * factor, controls.minZoom, controls.maxZoom);
    camera.updateProjectionMatrix(); controls.update(); cameraChanged();
  }
  function reset() {
    // Clear pending damping before restoring the exact home position.
    const damping = controls.enableDamping; controls.enableDamping = false; controls.update();
    controls.target.copy(CENTER); camera.position.copy(CENTER).add(OFFSET); camera.zoom = 1;
    controls.update(); controls.enableDamping = damping; resize();
  }
  function setMode(mode: "rotate" | "pan") {
    controls.mouseButtons.LEFT = mode === "rotate" ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN;
    controls.touches.ONE = mode === "rotate" ? THREE.TOUCH.ROTATE : THREE.TOUCH.PAN;
  }
  function pick(event: PointerEvent) {
    if (disposed || lost) return;
    const rect = canvas.getBoundingClientRect();
    pointer.set((event.clientX - rect.left) / width * 2 - 1, -(event.clientY - rect.top) / height * 2 + 1);
    scene.updateMatrixWorld(); raycaster.setFromCamera(pointer, camera);
    // Include the building so a hidden student cannot be picked through a wall.
    const hit = raycaster.intersectObjects([model, ...people], true)[0];
    if (hit?.object.userData.person && hit.instanceId !== undefined) {
      const person = data.poses[hit.instanceId]; if (person) callbacks.onStudent(person.id);
    } else if (hit?.object.userData.stationIds?.length) {
      const options = data.stations.filter((s) => hit.object.userData.stationIds.includes(s.id));
      options.sort((a, b) => Math.hypot(a.pos[0] - hit.point.x, -a.pos[1] - hit.point.z) - Math.hypot(b.pos[0] - hit.point.x, -b.pos[1] - hit.point.z));
      if (options[0]) callbacks.onStation(options[0].id);
    }
  }
  const down = (e: PointerEvent) => { taps.down(e.pointerId, e.clientX, e.clientY); canvas.focus({ preventScroll: true }); canvas.classList.add("is-dragging"); };
  const move = (e: PointerEvent) => taps.move(e.pointerId, e.clientX, e.clientY);
  const up = (e: PointerEvent) => { canvas.classList.remove("is-dragging"); if (taps.up(e.pointerId, e.clientX, e.clientY) && e.button === 0) pick(e); };
  const cancel = (e: PointerEvent) => { taps.cancel(e.pointerId); canvas.classList.remove("is-dragging"); };
  function keydown(e: KeyboardEvent) {
    if (["+", "=", "-", "_", "Home", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) e.preventDefault();
    else return;
    if (e.key === "Home") reset();
    else if (e.key === "+" || e.key === "=") zoom(1.2);
    else if (e.key === "-" || e.key === "_") zoom(1 / 1.2);
    else {
      const spherical = new THREE.Spherical().setFromVector3(camera.position.clone().sub(controls.target));
      spherical.theta += e.key === "ArrowLeft" ? .12 : e.key === "ArrowRight" ? -.12 : 0;
      spherical.phi = THREE.MathUtils.clamp(spherical.phi + (e.key === "ArrowUp" ? -.1 : e.key === "ArrowDown" ? .1 : 0), controls.minPolarAngle, controls.maxPolarAngle);
      camera.position.copy(controls.target).add(new THREE.Vector3().setFromSpherical(spherical)); controls.update(); cameraChanged();
    }
  }
  function visibility() { if (document.hidden) { cancelAnimationFrame(frame); frame = 0; } else invalidate(); }
  function contextLost(e: Event) { e.preventDefault(); lost = true; cancelAnimationFrame(frame); frame = 0; callbacks.onUnavailable(); }
  function changeMotion() { controls.enableDamping = !motion.matches; invalidate(); }
  canvas.addEventListener("pointerdown", down, true); canvas.addEventListener("pointermove", move, true);
  canvas.addEventListener("pointerup", up, true); canvas.addEventListener("pointercancel", cancel, true);
  canvas.addEventListener("keydown", keydown); canvas.addEventListener("webglcontextlost", contextLost);
  document.addEventListener("visibilitychange", visibility); motion.addEventListener("change", changeMotion);
  resize();

  return { update, zoom, reset, setMode, refreshLabels: cameraChanged, dispose() {
    if (disposed) return;
    disposed = true; cancelAnimationFrame(frame); observer.disconnect(); controls.dispose();
    canvas.removeEventListener("pointerdown", down, true); canvas.removeEventListener("pointermove", move, true);
    canvas.removeEventListener("pointerup", up, true); canvas.removeEventListener("pointercancel", cancel, true);
    canvas.removeEventListener("keydown", keydown); canvas.removeEventListener("webglcontextlost", contextLost);
    document.removeEventListener("visibilitychange", visibility); motion.removeEventListener("change", changeMotion);
    disposePeople();
    const geometries = new Set<THREE.BufferGeometry>(), materials = new Set<THREE.Material>();
    scene.traverse((object) => {
      if (object instanceof THREE.Mesh) { geometries.add(object.geometry); (Array.isArray(object.material) ? object.material : [object.material]).forEach((m) => materials.add(m)); }
      if (object instanceof THREE.InstancedMesh) object.dispose();
    });
    geometries.forEach((g) => g.dispose()); materials.forEach((m) => m.dispose()); sun.shadow.dispose(); renderer.dispose(); renderer.forceContextLoss();
  } };
}
