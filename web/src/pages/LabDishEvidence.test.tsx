import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TigerEvidence } from "./Lab";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const DISH = { name: "Butter Chicken", station: "tandoor", role: "entree", diet_tags: [], hot: true, similar_to: [], popularity_multiplier: 1 };

it("shows what look-alike dishes sold in Tiger and the new dish's spread", async () => {
  const bodies: unknown[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    expect(url).toBe("/api/dish-evidence");
    bodies.push(JSON.parse(String(init?.body)));
    return Response.json({
      neighbors: [{ dish_id: "a", name: "Mandarin Chicken", station: "street_food", role: "entree", distance: 0.45, nights: 6, eaten_per_night: 126, per_student: 0.0345 },
        { dish_id: "b", name: "Baked Chicken Leg", station: "tandoor", role: "entree", distance: 0.47, nights: 6, eaten_per_night: 239, per_student: 0.068 }],
      per_student_range: [0.0341, 0.068], neighbor_nights: 30, new_dish_sd: 0.42, neighbor_spread: 0.3, default_sd: 0.3,
      popularity_check: { dishes: 240, tau: 0.02, detected: false, note: "" },
    });
  });
  render(<TigerEvidence date="2026-11-13" apiUp dish={DISH} />);
  await screen.findByText(/In Tiger: dishes like this \(Mandarin Chicken, Baked Chicken Leg\) sold 3\.4–6\.8 portions/);
  screen.getByText(/over 30 recorded nights\. In plausible worlds its popularity varies ±42%/);
  expect(bodies[0]).toMatchObject({ date: "2026-11-13", name: "Butter Chicken", station: "tandoor", role: "entree" });
});

it("stays quiet without the API, for short names and when Tiger is down", async () => {
  const fetch = vi.fn(async () => new Response("{}", { status: 503 }));
  vi.stubGlobal("fetch", fetch);
  const { container, rerender } = render(<TigerEvidence date="2026-11-13" apiUp={false} dish={DISH} />);
  rerender(<TigerEvidence date="2026-11-13" apiUp dish={{ ...DISH, name: "Bu" }} />);
  await new Promise((r) => setTimeout(r, 500));
  expect(fetch).not.toHaveBeenCalled();
  rerender(<TigerEvidence date="2026-11-13" apiUp dish={DISH} />);
  await new Promise((r) => setTimeout(r, 500));
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(container.textContent).toBe("");
});
