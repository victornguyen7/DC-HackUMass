# DC What-If frontend redesign proposal

Status: Guided direction selected, November 13, 2026. The first implementation is now in the main app at `/hall`: illustrated isometric replay, Watch/Try/Compare stages, all station inspectors, continuous full-trace playback, and the existing top-down view. The original standalone concept remains a design reference. Items below that describe zoom, orbital 3D, or richer editing are future work. **Update (Nov 13, later):** Kitchen plan and Insights use the same top navigation; Insights has two tabs, Waste history (`/waste`) and **Similar nights** (`/similar-nights`, [SIMILAR_NIGHTS.md](SIMILAR_NIGHTS.md)), built in the visual system below. The local skill this plan cites (`codex-skills/skills/frontend-design/SKILL.md`) is no longer in the repo (the empty `codex-skills` entry was removed on 2026-11-14, ISSUES T4).

## Product direction

Make the simulation the front door. A first-time visitor should understand the dining hall, try one change, see the trade-off, and find a usable cook sheet without learning four separate workspaces.

Working audience assumption: both HackUMass judges and dining managers. Favor a short, compelling first visit while retaining direct access for returning managers.

The core journey is **Watch → Try a change → Compare → Review the kitchen plan**. The stages guide users but do not force repeat visitors through a wizard.

Use Image 1's clear sequence and stylized dimensional scene, and Image 3's spacious model viewport and contextual controls. Keep text brighter and controls more legible than Image 3. Keep the existing DC identity and Worcester context.

## What the current app tells us

- `Hall.tsx` renders a live SVG replay; its 3D option is a static image. A real 3D scene is new frontend work.
- The Hall initially selects both a station and a student. Their detail panels appear before the user has expressed interest in either.
- Camera controls, color modes, four playback speeds, event dots, four counters, pan inventories, and student histories compete for attention.
- Hall and Lab split two parts of the same decision: seeing a problem and testing a response.
- Breakfast, lunch, the serving-line camera, and optimization appear as unavailable controls.
- Some controls expose implementation details, including starting the API, to a user who is trying to plan dinner.
- React 18, Vite, TypeScript, Zustand, the API client, and replay functions already provide a useful foundation. A framework migration is not required.
- Results cover 30 nights. The animation shows one representative night. These are different contexts and must be labeled separately.
- Recorded waste is synthetic. Ranges reflect simulated night-to-night variation, not all uncertainty in the model's assumptions.

## Navigation

| Destination | Main job | Existing features it contains |
| --- | --- | --- |
| **Simulation** (default) | Watch, change, compare | Hall + the primary Lab workflow; saved scenarios; optional advanced editing |
| **Kitchen plan** | Decide what to cook and when | Dashboard production plan, quantities, staffing context, print and CSV |
| **Insights** | Understand history and assumptions | Waste Record ("Waste history" tab), synthetic back-test, **Similar nights** tab, model information, calibration link |

Comparison is a stage inside Simulation, not a fourth destination. Retain old route links through redirects and preserve date/scenario context across navigation. Keep saved scenarios in the scenario picker.

## The first screen

At a laptop width, give the hall roughly two-thirds of the work area and a contextual panel the remaining third. Use a slim top navigation instead of the permanent wide sidebar. Keep playback visible below the hall without scrolling.

Show:

1. Worcester, selected date, current scenario, and a visible **Simulated** label.
2. An isometric cutaway of the hall, framed to show the serving islands and seating.
3. A paused representative-night snapshot with a clear Play control.
4. A short contextual observation about the current snapshot, with a next action.
5. Three replay counters: students inside, students waiting, and empty pans. Keep counts separate from full-night projections.
6. One primary action: **Try a change**.

Use a small number of station labels at the default zoom. Show additional labels on focus or zoom. A station list provides a keyboard-accessible alternative to selecting the model. Start with no selected student or station.

The initial snapshot should contain recognizable activity without falsely labeling it as the peak. A separate **Jump to busiest period** action derives its destination from the trace.

## Interaction states

### Watch

The default inspector offers a plain-language explanation and the next action. Selecting a station replaces that content with queue length, food availability, and an action such as **Test another server here**. Selecting a student uses the same panel for their story. Closing details restores the previous context.

Playback has Play/Pause, a time scrubber, one speed menu, and a jump-to-busy-period action. Aggregate event marks into meaningful moments instead of displaying every pan event. Keep per-event diagnostics available under replay details.

### Try a change

Start with three supported scenarios:

- **Cook smaller batches** — examine waste and availability.
- **Add a Grill server** — examine waiting and faster depletion.
- **Test a busier night** — examine demand under the existing theme-night preset; disclose the exact +25% crowd assumption.

The panel shows the concrete settings before the user runs the comparison. **Advanced settings** contains per-dish batches, reorder points, other staffing changes, and menu edits. Plain-English input is an optional shortcut alongside explicit controls.

Use **Compare 30 nights** for computation and **Play replay** for animation. They are distinct operations. Cached comparisons should say they are ready; do not animate fabricated simulation progress. When settings change after a completed run, mark the result as belonging to the previous settings until recomputed.

### Compare

Present three trade-offs together: waste, waiting, and dish availability. Show median change and the 10th–90th percentile range, with the engine's per-metric verdict. Use plain labels with definitions available beside technical terms.

Keep first-choice failures and missed meals visible in an expandable student-outcomes section. Surface a worsening dietary-access or missed-meal flag prominently when present; it must not disappear merely because it is a secondary metric.

Do not manufacture a single overall winner or describe a preset as optimized. The engine does not implement an optimizer. Do not subtract marginal medians and call that the paired median change; use `vs_baseline.metrics`.

Example from the current Nov 13 bundle, extra Grill server:

- Paired median waste change: **−135 portions**, 10th–90th percentile **−266.2 to −47.4**.
- Paired median p90-wait change: **−2.84 minutes**, range **−3.513 to −1.914**.
- Paired median unavailable-dish time change: **+59.885 dish-minutes**, range **+38.502 to +106.773**.

The decision is a trade-off: shorter waits and less waste alongside worse availability. Dish-minutes are summed over dishes, not elapsed wall-clock minutes.

After comparison, offer **Review kitchen plan** and a separate, explicit **Use this plan for Nov 13** action. Make baseline restoration easy and show which scenario is adopted.

### Kitchen plan

Lead with the selected date and adopted plan. Group actionable cooking instructions by service period and station. Expand individual dishes for detailed batch assumptions. Keep Print and CSV easy to find. Returning managers can open this page directly.

### Insights

Give synthetic history its own clearly labeled context. Show the limitation of the back-test beside its conclusions: the simulated engine is being assessed against data generated from that engine. Do not imply measured savings at Worcester. Make sources and assumptions inspectable through a persistent Model information action.

## Feature reduction

| Treatment | Features |
| --- | --- |
| Always available in the simulation | Date, scenario, replay controls, primary scenario action, concise status, simulation label |
| On selection | Station inventory and queue history; student journey and narration |
| In view settings | Top-down camera, diet coloring, missed-first-choice coloring, detailed event markers |
| In advanced scenario settings | Per-dish controls, menu edits, popularity, plain-English interpretation details |
| In other destinations | Historical waste, full production tables, exports, calibration, detailed assumptions |
| Remove until supported | Breakfast/lunch switches, Optimize, serving-line camera, nonfunctional comparison promises |
| Defer beyond the first release | First-person navigation, heavy postprocessing, continuous split-screen 3D, decorative effects |

Keep the concise **Simulated** or **Synthetic history** label visible; reducing clutter must not remove provenance or uncertainty.

## Visual system

- **Simulation:** deep blue-charcoal stage, pale architectural surfaces, warm wood tables, restrained station highlights. Use depth, contact shadows, and clear silhouettes to create the visual impact.
- **Reading surfaces:** opaque, high-contrast panels. Kitchen plan and Insights can use a light theme with the same typography, spacing, and components.
- **Identity:** retain DC/Worcester and burgundy for primary actions; use amber for waiting/attention and a separate positive color with text labels. Color is never the only status cue.
- **Type:** one clear interface family, such as Manrope with system fallbacks. Use tabular numerals for changing values. Body text 14–16 px, secondary labels at least 12 px.
- **Spacing:** consistent 4/8 px rhythm, 16–24 px panel padding, restrained 8–12 px radii. Give the model more space than panel chrome.
- **Motion:** student movement explains the replay; camera transitions explain selection. Pause by default. Respect reduced motion; avoid automatic orbit, flickering warnings, or decorative particles.

The concept includes two arrangements: **Simulation studio**, with a dominant scene and contextual right panel, and **Guided workspace**, with the sequence and task panel on the left. Prefer Simulation studio for the stated references; use the second to assess beginner orientation.

## Implementation approach

1. **Restructure the workflow first.** Introduce the three destinations and shared simulation context; separate draft settings, completed results, adopted plan, and playback state. Keep the existing SVG renderer usable during development.
2. **Build the hall scene.** Add Three.js through React Three Fiber and compatible Drei helpers. The current React 18 app requires Fiber 8; Fiber 9 pairs with React 19. Resolve compatible versions together rather than installing an incompatible latest renderer.
3. **Reuse engine data.** Adapt `poseAt`, counters, and pan timelines to a rendering-neutral frame model. Draw existing trace positions; never invent a client-side simulation. Improve collision/path geometry separately and document approximate walking paths and assumed seating.
4. **Make the 3D scene practical.** Use an orthographic camera, constrained orbit, reset view, cutaway walls, instanced people/chairs/tables, reused materials, and limited shadows. Load the 3D code separately. Stop continuous rendering while paused and idle; keep high-frequency scene motion outside React component state.
5. **Preserve usability across devices.** Provide a real top-down fallback for weak GPUs or WebGL failure. On phones prioritize controls, a station list, and results; stack the scene and use a sheet for selection. Preserve keyboard access without requiring interaction with the canvas.
6. **Complete comparison and plan handoff.** Wire existing jobs, bundle fallback, saved scenarios, plan adoption, print, and CSV to the new workflow.

For optional synchronized visual comparisons, use the same seed and clock. Current bundled scenario traces use different median seeds, so placing them side by side does not isolate the decision's effect. The first release should use paired aggregate results plus a single clearly labeled replay; paired trace comparison requires additional trace selection or bundle work.

## Acceptance checks

- A new visitor can identify what is being simulated and locate Try a change without instruction.
- A visitor can run a preset, explain its main benefit and cost, and find the cook sheet in a short moderated task.
- One inspector is open at a time; the default does not expose a random student.
- No unavailable feature looks ready to use, and user-facing errors describe a recovery action without terminal commands.
- Results remain traceable to the selected date/scenario and visibly distinguish one replay from 30-night aggregates.
- Keyboard, focus, contrast, reduced motion, laptop sizing, and phone layout are checked.
- Benchmark the busiest replay on the actual demo laptop; target a stable 30 fps minimum, ideally 60, without sacrificing readable controls. This is a target, not an achieved measurement.
- Existing engine contracts, offline presets, scenario adoption, unit checks, production build, and browser smoke flows still pass after implementation.

## Research and skill selection

These are complementary resources, not an objective ranking or a reason to install every skill.

- **Local frontend-design:** `codex-skills/skills/frontend-design/SKILL.md` supplied an intentional aesthetic, tokens, interaction polish, and accessibility workflow. It is no longer in the repo (the empty `codex-skills` entry was removed on 2026-11-14, ISSUES T4).
- **Anthropic frontend-design:** https://github.com/anthropics/skills/tree/main/skills/frontend-design — useful for subject-specific art direction and avoiding an interchangeable template.
- **Vercel web-design-guidelines:** https://github.com/vercel-labs/agent-skills/tree/main/skills/web-design-guidelines and https://vercel.com/design/guidelines — useful for keyboard behavior, focus, layout, and interaction review.
- **Vercel React best practices:** https://github.com/vercel-labs/agent-skills/tree/main/skills/react-best-practices — use applicable client-side performance guidance; Next.js/server-specific guidance is not a migration requirement for this Vite app.
- **UI UX Pro Max:** https://github.com/nextlevelbuilder/ui-ux-pro-max-skill — optional reference for palettes, type pairings, and patterns. Its breadth does not replace task-specific workflow decisions.
- **NN/g progressive disclosure:** https://www.nngroup.com/articles/progressive-disclosure/ — show frequent actions first and make deeper controls discoverable.
- **React Three Fiber compatibility:** https://r3f.docs.pmnd.rs/getting-started/introduction
- **React Three Fiber performance:** https://r3f.docs.pmnd.rs/advanced/scaling-performance and https://r3f.docs.pmnd.rs/advanced/pitfalls
- **W3C motion guidance:** https://www.w3.org/WAI/WCAG22/Understanding/animation-from-interactions.html — support motion preferences; this particular success criterion is Level AAA.

No external skill has been installed. The proposed concept uses the existing bundle for numerical examples and an illustrated scene; it is not an implemented production redesign.
