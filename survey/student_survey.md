# Student Dining Survey (Worcester dinner)

Target: **about 3 minutes, anonymous, 50–100 responses.** Build it in Google Forms (turn **off** "Collect email addresses" and sign-in) and share it by QR code in the Worcester line and in class group chats.

Each question feeds one persona field or calibration target (ENGINE.md §7.3). The `column` is the field name in `data/survey_responses.csv`.

---

## Intro text (top of the form)

> **Help us make Worcester dinners better (3 min)**
> We're a HackUMass team building a tool that helps dining staff cook the right amount: fewer empty pans, less wasted food. This survey is anonymous. We don't collect your name, email or student ID. Answers are used only in aggregate for this project. You can skip any question.

---

## Section 1: About you

**Q1. Which year are you in?** · `year`
- First-year
- Sophomore
- Junior
- Senior
- Grad student
- Prefer not to say

**Q2. Where do you live?** · `res_area`
- Southwest
- Central
- Northeast
- Orchard Hill
- Sylvan
- Commonwealth Honors College Residential Community
- North Apartments
- Off campus
- Prefer not to say

**Q3. Which dining common do you go to most for dinner?** · `main_dc`
- Worcester
- Franklin
- Hampshire
- Berkshire
- I rarely eat dinner at a DC

**Q4. Do you follow any of these diets? (check all that apply)** · `diet_*` (one 0/1 column each)
- Vegetarian
- Vegan
- Halal
- Kosher
- Gluten-free
- Food allergy that limits what I can eat
- None of these

---

## Section 2: When you eat

**Q5. On Monday / Wednesday / Friday, when do you usually start dinner?** · `dinner_time_mwf`
- Before 4:30 pm
- 4:30–5:00
- 5:00–5:30
- 5:30–6:00
- 6:00–6:30
- 6:30–7:00
- 7:00–7:30
- 7:30–8:00
- After 8:00
- It changes a lot

**Q6. On Tuesday / Thursday, when do you usually start dinner?** · `dinner_time_tuth`
(same options as Q5)

**Q7. What usually decides when you go to dinner?** · `timing_driver`
- Right after my last class
- When my friends are going
- When I get hungry
- I go when I think it's less crowded
- My work / practice / club schedule

**Q8. How long do you usually stay at dinner?** · `dwell`
- Under 15 minutes / I take it to go
- 15–30 minutes
- 30–45 minutes
- 45+ minutes

**Q9. Do you usually eat with others?** · `group`
- Alone
- With 1–2 people
- With 3+ people

---

## Section 3: How you pick food

**Q10. On your first trip, how many different items do you usually take?** · `plate_items`
(Count each dish as one item: an entrée, a side, a salad, a slice of pizza, a dessert…)
- 1
- 2
- 3
- 4 or more

**Q11. How often do you go back for seconds or another item?** · `seconds`
- Never
- Sometimes
- Usually
- Almost always

**Q12. Your favorite dish tonight has a line. What's the longest line you'd join for it?** · `patience`
(About 5 people ≈ 2 minutes)
- I wouldn't wait in a line, I'd pick something else
- Up to 5 people
- Up to 10 people
- Up to 20 people
- I'd wait however long it takes

**Q13. You get to the station and your first-choice dish is gone. What do you usually do?** · `stockout_action`
- Take something else at the same station
- Go to a different station
- Wait for the next batch
- Leave / eat somewhere else

**Q14. Do you check the menu before you go (website or app)?** · `checks_menu`
- Always
- Sometimes
- Never

---

## Section 4: Which would you pick? (pairwise)

> **Instructions shown to students:** "Imagine both dishes are out at Worcester tonight and there's no line. Which would you put on your plate?"

**Setup rules for the person building the form:**
- Use **this week's actual Worcester dinner menu** (from `data/menus/`). Replace the placeholder dishes below on survey day.
- Use **10 pairs**. Each dish should appear **2–3 times**, and every dish should connect to the others through some chain of pairs (otherwise Bradley-Terry can't place it on the same scale).
- Shuffle the order of questions **and** the left/right position of the dishes (Google Forms: "Shuffle option order").
- Options for every pair: **Dish A · Dish B · No preference / I wouldn't eat either**. "No preference" answers are dropped from the fit.
- Include **2 vegetarian-vs-vegetarian pairs**, so the vegetarian dishes get ranked among themselves too.

| # | `column` | Dish A (placeholder) | Dish B (placeholder) |
| --- | --- | --- | --- |
| P1 | `pair_01` | General Tso's chicken | Meatloaf |
| P2 | `pair_02` | Meatloaf | Cheese pizza |
| P3 | `pair_03` | Cheese pizza | Vegetable stir-fry |
| P4 | `pair_04` | Vegetable stir-fry | General Tso's chicken |
| P5 | `pair_05` | Grilled chicken sandwich | Pasta marinara |
| P6 | `pair_06` | Pasta marinara | Black bean burger |
| P7 | `pair_07` | Black bean burger | Vegetable stir-fry |
| P8 | `pair_08` | Grilled chicken sandwich | General Tso's chicken |
| P9 | `pair_09` | Chicken curry | Meatloaf |
| P10 | `pair_10` | Chicken curry | Pasta marinara |

Save the pair → dish mapping as `survey/pairs_<date>.json` so the analysis knows which dishes each column refers to.

---

## Section 5: New dishes (checks the AI estimates)

**Q15–Q17. How likely would you be to take each of these if Worcester served it?** · `new_dish_1..3`
Scale: 1 (definitely not) – 5 (definitely)

- Q15. *Chicken tikka masala with rice* (placeholder)
- Q16. *Korean BBQ beef bowl* (placeholder)
- Q17. *Lentil shepherd's pie (vegan)* (placeholder)

Use the dishes the team plans to demo in the What-If page. These answers are **not** used to fit the model. They're held out to check whether the LLM's new-dish priors were reasonable.

---

## Section 6: Optional

**Q18. (Optional) One dish you wish Worcester served more often:** · `wish_dish` (short text)

> "Thanks! Your answers stay anonymous and are used only in aggregate."

---

## How each answer is used

| Question | Persona field / use |
| --- | --- |
| Q1 year, Q2 area, Q3 main DC | Persona shares (step 1 totals); filter to Worcester diners for the fit |
| Q4 diet | `diet` share totals (step 1); diet eligibility in the engine |
| Q5–Q6 dinner time, Q7 driver | `arrival_bias`; checks the arrival waves against class-block end times |
| Q8 dwell | `dwell` distribution; seating pressure |
| Q9 group | Future: groups arriving together (P2) |
| Q10 plate items | `plate_size` distribution |
| Q11 seconds | `seconds_prob` |
| Q12 patience | `patience` distribution (convert to students in line: 0, 5, 10, 20, 40) |
| Q13 stockout action | Substitution behavior (same station / other station / wait / leave) |
| Q14 checks menu | Future: whether a featured dish draws extra crowd (P2) |
| P1–P10 pairwise | Per-dish correction factors (step 2), 20% held out for the check (step 3) |
| Q15–Q17 new dishes | Held-out check of the LLM new-dish priors |
| Q18 wish dish | Ideas for What-If demo scenarios; not modeled |

## Data handling

- No names, emails, student IDs, or exact dorm/room. Residential **area** only.
- Before analysis, drop free-text answers that contain anything identifying.
- Report only totals and model fits. Never show an individual response.
- If results might be published beyond the hackathon, check with UMass IRB first.
