import { describe, expect, it } from "vitest";
import { OrthographicCamera, Vector3 } from "three";
import { fitHallCamera } from "./hall3d";
import { FOOTPRINT } from "./hallGeometry";
import { createTapTracker, layoutStationLabels } from "./hallSceneInteraction";

describe("3D touch selection", () => {
  it("accepts a tap with finger jitter but never a drag that returns to its start", () => {
    const taps = createTapTracker();
    taps.down(1, 100, 100); taps.move(1, 103, 102);
    expect(taps.up(1, 103, 102)).toBe(true);
    taps.down(1, 100, 100); taps.move(1, 125, 110); taps.move(1, 100, 100);
    expect(taps.up(1, 100, 100)).toBe(false);
  });
  it("doesn't select a student after a pinch, two-finger pan, or cancelled touch", () => {
    const taps = createTapTracker();
    taps.down(1, 100, 100); taps.down(2, 200, 200);
    expect(taps.up(2, 200, 200)).toBe(false);
    expect(taps.up(1, 100, 100)).toBe(false);
    taps.down(3, 100, 100); taps.cancel(3);
    expect(taps.up(3, 100, 100)).toBe(false);
    taps.down(4, 50, 50);
    expect(taps.up(4, 50, 50)).toBe(true);
  });
});

describe("3D camera framing", () => {
  it.each([[690, 290], [850, 350], [1490, 530], [360, 350]])("fits the entire hall at %i × %i without changing the user's zoom", (width, height) => {
    const camera = new OrthographicCamera(-50, 50, 30, -30, .1, 300);
    const center = new Vector3(31.75, 0, -17.25);
    camera.position.copy(center).add(new Vector3(24, 65, 100)); camera.lookAt(center);
    fitHallCamera(camera, width, height);
    for (const [x, y] of FOOTPRINT) for (const h of [-.65, 3]) {
      const p = new Vector3(x, h, -y).project(camera);
      expect(Math.abs(p.x)).toBeLessThan(1);
      expect(Math.abs(p.y)).toBeLessThan(1);
    }
    camera.zoom = 2;
    fitHallCamera(camera, width + 100, height);
    expect(camera.zoom).toBe(2);
  });
});

it("separates crowded labels and hides off-screen anchors instead of stacking them at the edge", () => {
  const labels = layoutStationLabels([
    { id: "grill", label: "Grill", x: 300, y: 180, visible: true },
    { id: "tandoor", label: "Tandoor", x: 310, y: 182, visible: true },
    { id: "soups", label: "Soups", x: 305, y: 184, visible: true },
    { id: "sushi", label: "Sushi", x: -20, y: 180, visible: true },
  ], 690, 290);
  expect(labels[3].visible).toBe(false);
  for (const a of labels.filter((l) => l.visible)) {
    expect(a.left).toBeGreaterThanOrEqual(8);
    expect(a.left + a.width).toBeLessThanOrEqual(682);
    expect(a.top + a.height).toBeLessThanOrEqual(206);
    for (const b of labels.filter((l) => l.visible && l.id !== a.id)) {
      const overlaps = a.left < b.left + b.width && a.left + a.width > b.left && a.top < b.top + b.height && a.top + a.height > b.top;
      expect(overlaps).toBe(false);
    }
  }
});
