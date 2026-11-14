# Worcester data: sources and assumptions

Updated 2026-11-12; late night and the 9 pm switch added 2026-11-13 (engine 0.4.1); crowd by date, carving line and late-night taper (0.4.2); kitchen capacity, empty-pan wait and seats (0.5.0); allergies, pescetarian, Noodle Bowl per dish, swipe queue, groups, pan sizes and the forecast-reorder preset (0.6.0). Built from the research uploads (attendance, population, waste, tools ecosystem, floor plan) and revised after the public-source audit of this file (`raw/research/worcester_simulation_assumption_evidence.json`, split per assumption in `sources/audit/assumptions/A01…A17`). File index: `data/README.md`. Citations: `data/sources/registry.json`. What to ask UMass Dining for: `sources/audit/data_requests.json` (R01–R08).

## Sourced inputs the engine uses

| Item | Value | Source |
| --- | --- | --- |
| Menus, Nov 13–20, 2026 | Station names, items, serving sizes, diet labels, allergens, carbon ratings | umassdining.com snapshot, `raw/menu/` (split per meal in `menu/<date>/<meal>.json`) |
| Stations | 12 real dinner stations | Same menu snapshot |
| **Station positions** (closed, design drawing) | `stations.json → pos`, from `layout/floor_plan.json` | Published second-floor design drawing, `raw/layout/worcester_second_floor_plan.png` (PDF metadata July 2021; original design date and today's as-built layout unverified). Scale from stair treads, checked against a structural bay; about ±10%. Footprint ≈ 63.5 × 34.6 m. Grill, Latin, Street Food, Noodles, Sushi and Dessert are labeled on the drawing. Tandoor is only the east frontage of the center island, facing the main dining room (team member, 2026-11-13), where the drawing labels "Naan" (badge 10) |
| **Entrance / seating zones** (approximate) | Foyer (badge 15); two seating zones in `layout/floor_plan.json → seating_zones`: main dining room 200 (284 seats) and the indoor back seating room south of the check-in desk (120 seats), reached through doors 207A (turn left after check-in) or 201B/201C (behind Display) | Same drawing; back room is indoor seating and its doors are team-confirmed (2026-11-13). Tables are not drawn, so table layout and seat counts are assumed. Students pick a zone in proportion to seats; walks into the back room go through the nearest door |
| **Vegetarian and vegan shares** (closed) | vegetarian 0.083, vegan 0.012 (were 0.12 and 0.03); regular 0.435 absorbs the difference | UMass Dining Customer Satisfaction and Eating Habits Survey, Spring 2022, n = 1,550, 25.8% response (`sources/population/diet_patterns.json`). Historical survey, not a measured 2026 share |
| Walking speed | 72 m/min | ~1.2 m/s, standard |
| **Dinner end** (moved from assumptions) | 9:00 pm (`dc.json → close_min` 1260) | Worcester late night is advertised to start at 21:00 (umassdining.com Worcester page, audit fact F03). The page's heading and text disagree on which days have late night, so confirm Fri/Sat |
| **Late night** (engine 0.4.1) | 9:00 pm–12:00 am (`dc.json → late_night.close_min` 1440), modeled on every date with a published late-night menu (all 8 snapshot dates); serving stops at midnight, seated students may stay | Interval: umassdining.com Worcester page, audit fact F03. **Days not confirmed**: the page heading says Sunday–Saturday, the text says Sunday–Thursday. **Team guesses (2026-11-13):** about 1,000 students; snack plates of 1 item (60%) or 2 (40%); arrivals peak ~9:15 and ~9:55 pm and taper so fewer than 30 students are inside at 11:30 pm (about 20 in the model on every date). Late-night stations reuse dinner staffing (Grill 2, Display 4, Mediterranean 2), unconfirmed |
| **9:00 pm switch** (engine 0.4.1, model choice) | Dinner dishes take no new students at 9:00; lines already formed are served from what is left for up to 30 minutes | Not observed; chosen so dinner students in line at 9 pm still get food |
| **Crowd by date** (engine 0.4.2, E5) | `crowd_by_date.json`: dinner Fri ×0.9, Sat ×0.85, Sun ×0.95, Mon–Thu ×1.0; Nov 18 Friendsgiving ×1.15, Nov 20 TCSA dinner ×1.1; late night ×1.0 | **Placeholder guesses (2026-11-13).** No measured lift exists (`calendar.json → crowd_modifier` stays null). Replace with swipes by date (R01/R08) |
| **Nov 18 carving line** (engine 0.4.2, E7) | Carved turkey served at a staffed `carving` station: 2 carvers, 0.5 min a plate, roasts cooked in batches | Guess; confirm how Worcester serves the Friendsgiving carving |
| **Late-night kitchen taper** (engine 0.4.2) | Once fewer than 1.5 batches are still to be eaten, top-up batches of 25%; no spare batches | Tuning choice: late-night waste ~41% → ~20% with similar stockouts. Not staff-confirmed |
| **Kitchen capacity** (engine 0.5.0, IMPROVEMENTS 1.3) | 12 dinner / 4 late-night cooks; oven 14, steamer 8, kettle 4, flat-top 6, wok 5, fryer 3, tandoor 5 units (one batch each); hands-on prep per batch: entree 10, starch 5, side 6, soup 10, dessert 10, bread 3 min (inside cook_min) | **Placeholder guesses (2026-11-13)**, sized from unconstrained peak use so the baseline barely changes (cooks ~63% busy). Ask the kitchen (R02/R07) |
| **Empty-pan wait** (engine 0.5.0, 1.2) | Up to 2 min (6 min for the first choice) at the counter when the dish is empty but a batch is cooking | Guess |
| **Seats** (engine 0.5.0, 2.4) | `seat_limits` off: 284 + 120 seats are assumed and the peak wants 530–680; seats unlimited until counted, `peak_seated` reported | Count seats per zone, then turn limits on |
| **Allergies** (engine 0.6.0, N8) | Tree nuts 2.73%, peanuts 2.3%, gluten 1.32% (labels Gluten + Wheat), shellfish 1.0%, milk 0.93%, fish 0.43%, egg 0.38%, sesame 0.3%, soy 0.23%, corn 0.04%, drawn independently per student; dishes labeled with the allergen are never chosen | UMass Dining survey 2022–2023 (`sources/population/allergies.json`). Allergy and intolerance are mixed; fruits/vegetables (0.38%) have no menu label |
| **Pescetarian** (engine 0.6.0, N8) | 2.0% persona (from regular), eats vegetarian dishes and fish/seafood dishes with no meat in the name | Spring 2022 UMass Dining survey (`sources/population/diet_patterns.json`); the meat-name rule is ours |
| **Noodle Bowl per dish** (engine 0.6.0, E6) | Hot Pot, Pho, Shrimp Tom Yum, Taiwan Pork 2×; ramen 1× (`overrides.json → dish_popularity`) | Team, 2026-11-13 |
| **Swipe queue, groups, pan sizes** (engine 0.6.0) | 2 card readers at 4 s; 40% of social students in groups of 2/3/4 (50/30/20%) arriving within 10 min, sitting together; line pan holds entree 40, starch 50, side 40, soup 60, dessert 48, bread 40 portions, 0.5-min swap from the hot-hold cabinet | **Placeholder guesses (2026-11-13)** |
| **Forecast reorder preset** (engine 0.6.0) | Factor 1.2, look-ahead cook time + 5 min, safety z 1.65, re-check every 5 min | Model choice; z tuned on Nov 13/17 (1.65 vs 3 and 4) |
| **Dinner start** (moved, team-confirmed) | 4:30 pm (`open_min` 990) | Team member who eats at Worcester, 2026-11-12. Not yet on an official page |
| **Street Food service mode** (moved, team-confirmed) | Pre-cooked pans, plated by staff: `to_order: false`, service 0.45 min/item, batch-planned by `dcsim prepare` | Team member, 2026-11-12. Consistent with the 2020 tour (made-to-order stir-fry was the separate Wok station). The 0.45 min service time is still a guess |
| **Sushi service mode** (moved, team-confirmed) | Made to order (`to_order: true`) | Team member, 2026-11-12; matches the 2020 tour (Nori rolls made to order) |
| **Display service mode** (moved, team-confirmed) | Self-serve from pre-made pans: `to_order: false`, 0.3 min/item, batch-planned | Team member, 2026-11-12. Service time is a guess |
| **Mediterranean, Seasons, Soups positions** (moved, team-confirmed) | Mediterranean = left end of the Pizza counter run; Seasons = Greens counter; Soups = salad-bar side of the center island | Team member, 2026-11-12; points read off the floor plan (`layout/floor_plan.json`) |
| **Hot-station servers** (moved, team-confirmed) | 2 servers at Grill, Tandoor, Latino, Mediterranean, Seasons, Street Food at the 7–8 pm peak | Team member, 2026-11-12. Off-peak staffing not asked; the model uses 2 all night |
| **Hot-station service time** (moved, team-confirmed range) | 30–60 s per plate, modeled as 0.5 min per item (diners take 1–2 items per station) | Team member, 2026-11-12. The per-item split is our conversion |
| **Sushi staffing and speed** (moved, team-confirmed range) | 2–3 makers, 2–3 min per roll; modeled as 3 makers, 2.5 min per item | Team member, 2026-11-12. 3 makers chosen because the model's Sushi line was already longer than observed |
| **Favorite stations** (moved, team-confirmed) | Street Food, Noodle Bowl, Grill, Latino at about 2× an ordinary station: `overrides.json → station_popularity` = 2.0, which the kitchen plan also knows | Team member, 2026-11-12. "About 2×" is their estimate; the model applies it to each dish at the station |
| **Noodle Bowl staffing and speed** (moved, team-confirmed) | 2 servers, 0.6 min per bowl | Team member, 2026-11-12: under 1 minute per order, 2 staff at peak |
| **After leaving a long line** (moved, team-confirmed) | Student goes to another hot station (next choice), then comes back once for their favorite after eating (`dc.json → retry_balked_favorite`) | Team member, 2026-11-12 |
| **Going back for more** (moved, team-confirmed) | About 50% of students go back (persona `seconds_prob` weighted average 0.496, was ~0.28) | Team member: students "often" go back, 2026-11-12. The split across personas is a guess |
| **Dish return area** (moved, team-confirmed) | West end, by dish room 230 | Team member, 2026-11-12. The exact opening isn't marked on the drawing, so the point is approximate |

Effect of the survey diet shares alone on Nov 13 (10 nights): wasted portions 1,782 → 1,730, stockout-min 99 → 103.

Effect of the 2026-11-12 team answers (crowd 3,000, later peaks, Street Food batch-cooked) on Nov 13: students 1,801 → 3,012; wasted portions 1,730 → 1,746; **stockout-min 103 → 192**; **missed first choice 12.7% → 27.9%**; missed hot meal 78 → 221; waste 27% → 18% of cooked portions. The planner calibration no longer gives ~27% waste and ~100 stockout-min (see Planner rules).

Nov 13 after all 2026-11-12 answers (planner setting A, slower service, Sushi 3 makers, station favorites, favorite-dish patience, ~50% seconds, come back for a favorite after leaving its line; engine 0.3.1): students 3,012; wasted portions 2,918; stockout-min 118; missed first choice 24.5%; p90 peak wait 17.4 min; missed hot meal 424; longest lines 28–35.

Metric definitions changed in engine 0.3.2 (2026-11-12): **never got 1st choice** = share of students who did not get their first choice at any point in the visit, return trips included (was: not on the first plate); **missed meal** = students who got nothing, or whose first choice was hot and who left with no hot food (was: anyone without a hot item, including ~200 who chose a cold dish). Same Nov 13 run under the new definitions: never got 1st choice 21.4%, missed meal 234.

## Verified facts (context, not engine inputs)

| Fact | Value | Source |
| --- | --- | --- |
| Building hours | 07:00–24:00, Nov 13–15 (the reviewed table stops at Nov 15) | umassdining.com/hours (F01, F02). Building hours, not the dinner window |
| Weekdays | Nov 14–15 are Saturday–Sunday; the rest are weekdays | Calendar arithmetic |
| Scheduled events | Nov 18 Friendsgiving dinner at all dining commons incl. Worcester; Nov 18 home football vs Akron (kickoff not announced); Nov 20 TCSA dinner at Worcester; Nov 14 VSA Food Fair (Hampshire); Nov 19 African Student Dinner (Berkshire, late night) | UMass Dining November calendar; UMass football notice. In `worcester/calendar.json` |

## Still assumptions

The audit found **no replacement value for any of these** (every `replacement_value` is null).

| Item | Value now | What the evidence says | Why it stays an assumption | Replace with |
| --- | --- | --- | --- | --- |
| Expected students (A01) | **3,000** per dinner (was 1,800) | Team member says a normal weekday dinner is busier than 1,800, roughly 2,500–4,000 (2026-11-12); 3,000 is a point picked inside that range. Context: about 12,000 meals/day (Boston Globe, 2023) | A rough impression, not a count; the exact number is a pick | R01 attendance export |
| Arrival waves (A03) | Peaks **6:20 / 7:30 / 8:30 pm** (shares 0.25 / 0.35 / 0.25, widths 15 / 20 / 15 min) + 15% flat. The old 5:20 pm peak was dropped | Team member: worst rushes are 6:00–6:45, 7:00–8:00 and 8:00–9:00 pm, with no early rush; **7:00–8:00 is the biggest** (2026-11-12) | Peak windows and the biggest rush are observed; the exact shares and widths are guesses | R01 time-binned admissions |
| Day-to-day crowd (A04) | Same crowd every date | Weekend and event dates are verified (see above), but not their effect on attendance | No measured event lift or weekend effect; events elsewhere may pull diners away | R08 matched event-night counts |
| Display station slot (A06) | Teaching kitchen (badge 14) | Teaching kitchen = instruction, recipe testing, guest chefs (2020 tour) | Mapping is weak; marked `assumed` in `layout/floor_plan.json` | R03 / photo walk |
| Servers / service time at other stations (A08) | Noodle Bowl 2 servers, 0.6 min/item; self-serve Soups, Breads, Desserts, Display spots and times in `stations.json` | Hot stations and Sushi are team-confirmed (above). Cook job postings: staff move within a station and watch queues | Not asked yet | R02 |
| Line lengths (calibration target) | Model peak (Nov 13): Grill 35, Latino 33, Sushi 32, Street Food 28; similar on Nov 14/16/17 | **Team member: longest lines are 30–40 people, at Street Food and Noodle Bowl** (2026-11-12) | Lengths now match. Noodle Bowl's speed and staffing are confirmed, and its line **depends on the dish**, so Hot Pot (Nov 13) may simply not be a crowd dish. The station-wide 2× for Noodle Bowl may belong on specific dishes instead | Which Noodle Bowl dishes draw crowds → `overrides.json → popularity` |
| Favorite-dish patience | `dc.json → favorite_patience_mult` 3.0: a student will wait in a line 3× longer than usual when it serves their first choice | Team member: "wait if I really want that dish, but I can go elsewhere" (2026-11-12) | The behavior is observed; the 3× size is a guess | R05 survey |
| Item roles (A09) | Rule-based classifier + `overrides.json` | — | — | R04 staff review |
| Popularity (A10) | Role prior ÷ √(same-role items at the station) × staff multiplier | 2023 slides: Chinese 37%, Mediterranean 32.8%, American Southern 32.2%, Latino 29.9%, Indian 24.4%, Korean 23.2%. Sushi a campus favorite (2022) | Questionnaire wording, sample and response design are unknown; the scores sum past 100% and are not dish or meal shares. No Worcester pairwise-choice data exists for Bradley-Terry | R04 survey / consumption data |
| Staff popularity multipliers (A11) | `overrides.json → popularity` (empty) | — | — | R04 |
| Halal share (A12) | 0.07 | Halal is offered; a 2020 tour names certified halal at **Tandoor** (consistent with the halal persona's tandoor affinity). No prevalence figure | Availability ≠ share who need it; nationality is not a proxy | R05 survey |
| Athlete share (A13) | 0.10 | > 5,700 intramural participants in 2025 (~¼ of undergrads) | Participation ≠ high-protein preference | R05 |
| Rushed, social and other persona attributes (A14) | Hand-written | One student blog names time before class, avoiding crowds, checking the livestream, bus/residence proximity and meeting friends as motives | Motives, not frequencies. **Model limit:** the engine forces persona shares to sum to 1, but "rushed" and "social" can overlap. LLM-made personas are synthetic, not a survey substitute | R05 |
| Baseline kitchen plan (A15) | `kitchen/<date>_dinner.json` from `dcsim prepare` | Metafoodx tracked 1.79 M lb of production in FY2026 (16 systems, 6 locations); cooks work from prep sheets and par levels | No public production sheet or Worcester export; no data access | R06 planned vs actual production |
| Planner rules (A16) | Batch ≈ demand in one hold time; reorder = **1.2** × peak rate × cook time (was 0.8; changed 2026-11-12 to match the team observation); last batch 20 min before close. When chosen (engine 0.3.x, 2026-11-12): Nov 13 72 stockout-min and 30% wasted (vs 204 / 18%; chosen over 40 min / 33% and 0 min / 40% to keep waste near the original ~27% tuning). **Now (engine 0.5.0, Nov 13, median of 10 nights):** dinner 54 stockout-min and 29% of cooked portions wasted; late night (taper) 24 min and 21%. Checked by `engine/tests/test_golden.py` | **Team member (2026-11-12): on a normal day food rarely runs out late; Street Food sometimes runs out when a popular dish is on.** The model runs out too much. Campus waste 7.19 oz/swipe (FY2026), 7.33 (FY2025) | **External campus benchmark only, not a bound** on Worcester dinner line waste. Compare only after converting portions with measured item weights and matching location, dates, waste stream and denominator | R06, R07 |
| Cook / hold times by role (A17) | `kitchen_defaults.json` | — | Food-safety limits are not quality-hold times | R07 batch/time study |

## Synthetic waste history and back-test (SYNTHETIC, for the Waste Record page)

No real Worcester waste or production records are available, so `dcsim waste-gen` generates 6 weeks of "recorded" nights (Oct 1 – Nov 11, 2026) with the same engine plus noise. Everything below is a guess. Hidden values are kept in `data/synthetic/worcester/_ground_truth.json` and never shown in the UI.

| Generator setting | Value | Note |
| --- | --- | --- |
| Menus for past nights | Real Nov 13–20 menu with the same weekday | No past menus were captured |
| Crowd swing | × lognormal, sd 8%; rain × 0.94 (25% of nights), cold × 0.97 (15%); event night × 1.15 (7%) | Weather and event effects are invented |
| Staff over-prep | Every batch 0–20% bigger than planned (0–5% on adopted nights) | **Drives most of the back-test headline:** of the 6.4% reduction, about 4.7 points come from "cooking to the forecast" and 1.6 from the plan's changes |
| Plate waste | 6–12% of served weight per night, ×0.6–1.4 per dish | Shown separately; not affected by planning |
| Portion weights and costs | Menu serving size when it's in oz, else `portion_defaults.json` by role; cost per portion by role ($0.20–$1.60) | Estimates, not FoodPro costs |
| Recommended plan | `smaller_batches` preset | Until the optimizer exists |
| Adopted nights | 3 in the last two weeks (Oct 29, Nov 2, Nov 6) | Forecast range includes ±12% crowd error |

Back-test result, engine 0.4.1 with late night (2026-11-13): **waste 5.9% lower (range 2.5–7.3%)**, plan wins 39 of 39, about 404 lb and $1,167 per week; stockouts 93 min/night under the plan vs 77 recorded. Earlier result, engine 0.3.4, dinner only: back-test result (39 non-adopted nights, 10 simulated nights each; re-run 2026-11-13, engine 0.3.4): **waste 6.4% lower (range 1.3–8.2%)**, plan wins 34 of 39 nights, about 326 lb and $824 per week (range −$11 to $1,285), **but about 16 more stockout-minutes per night than recorded** (75.7 vs 59.6; +1.3 vs the baseline plan cooked to the forecast). All three adopted nights land inside their forecast range. Per-dish results (`backtest.json → dishes`) are mixed: some dishes waste more under the plan (e.g. Halal Burger Patty +15%, Vegetable Spring Rolls +9%) while the total drops.

## Context kept for the dashboard and write-up (not engine inputs)

- Waste trend, destinations (81.3% digestion / 10.1% donation / 8.6% reuse of tracked overproduction, FY2026) and the FY2027 −10% target: `sources/waste/`.
- Vendor claims (Metafoodx −51.7% overproduction at Harvest Market over 12 weeks) are **vendor-reported and site-specific**; never apply them campus-wide or to Worcester.
- Allergy/intolerance prevalence (10.04% total, 2022–23) is in `sources/population/allergies.json`. The engine does not model allergens yet.
- Simulated savings must be labeled simulated and kept apart from published effects (`sources/ecosystem/data_flow.json → measurement_contract`).
- Substitutes the audit rules out (other universities' service times, shift hours as meal hours, scanner speed as service time, etc.): `sources/audit/corrections.json`.

Known gaps: add-ons don't consume stock; the Nov 18 carving line (engine 0.4.2) is a guess; one dessert-station item ("Make Your Own Waffles") is modeled as dessert; seating is two zone centroids (no per-table seats or capacity limit yet); pescetarian (2.0%) has no diet rule; persona shares can't overlap.
