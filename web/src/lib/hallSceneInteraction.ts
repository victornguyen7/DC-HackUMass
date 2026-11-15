/** Keep a drag or a multi-touch gesture from also selecting a student. */
export function createTapTracker() {
  const pointers = new Map<number, { x: number; y: number; moved: boolean }>();
  return {
    down(id: number, x: number, y: number) {
      pointers.set(id, { x, y, moved: pointers.size > 0 });
      if (pointers.size > 1) pointers.forEach((p) => { p.moved = true; });
    },
    move(id: number, x: number, y: number) {
      const p = pointers.get(id);
      if (p && Math.hypot(x - p.x, y - p.y) > 7) p.moved = true;
    },
    up(id: number, x: number, y: number) {
      const p = pointers.get(id);
      pointers.delete(id);
      return !!p && !p.moved && Math.hypot(x - p.x, y - p.y) <= 7;
    },
    cancel(id: number) { pointers.delete(id); },
  };
}

export type LabelAnchor = { id: string; label: string; x: number; y: number; visible: boolean };
export type LabelPosition = LabelAnchor & { left: number; top: number; width: number; height: number };

/** Screen-space placement. Off-screen stations stay off screen when zoomed in. */
export function layoutStationLabels(anchors: LabelAnchor[], width: number, height: number): LabelPosition[] {
  const placed: LabelPosition[] = [];
  return anchors.map((a) => {
    const w = Math.min(width - 16, a.label.length * 6.2 + 40), h = 44;
    const visible = a.visible && a.x >= 0 && a.x <= width && a.y >= 0 && a.y <= height - 80 && height > 130;
    const clampX = (x: number) => Math.max(8, Math.min(width - w - 8, x));
    const clampY = (y: number) => Math.max(8, Math.min(height - h - 84, y));
    let left = clampX(a.x - w / 2), top = clampY(a.y - h - 12);
    const overlaps = (x: number, y: number) => placed.some((b) => x < b.left + b.width + 5 && x + w + 5 > b.left && y < b.top + b.height + 5 && y + h + 5 > b.top);
    if (visible && overlaps(left, top)) {
      let best = Infinity;
      for (let y = 8; y <= height - h - 84; y += h + 6) {
        for (let x = 8; x <= width - w - 8; x += 10) {
          const distance = (x - (a.x - w / 2)) ** 2 + (y - (a.y - h - 12)) ** 2;
          if (distance < best && !overlaps(x, y)) { left = x; top = y; best = distance; }
        }
      }
    }
    const label = { ...a, visible, left, top, width: w, height: h };
    if (visible) placed.push(label);
    return label;
  });
}
