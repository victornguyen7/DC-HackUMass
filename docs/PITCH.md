# Pitch: Dineamics · Worcester (HackUMass 2026 judging)

Tracks we're entering: **[HackUMass] Best UMass Hack** · **[MLH] Best Use of Gemini API** · **[MLH] Best Use of Vultr** · **[Sentient OS] Best Agentic Hack**
Judged on: **Innovation · Functionality · Practicality · Presentation**

Live link: **https://dineamics.compare** (Vultr, GoDaddy Registry domain). Use the date **Fri Nov 13** for the hall and Lab, **Wed Nov 18** (Thanksgiving dinner) for the 4,000-night beat.

---

## 1. The one-liner (memorize it)

> **"Every night, a dining hall kitchen makes a bet it can't take back: how much food to cook. Dineamics lets them play tonight out a thousand times first, on the real menu with simulated students, so the mistake happens in a computer instead of in a trash can or a 40-person line."**

Ten seconds at a sponsor table:
> "A flight simulator for a dining hall kitchen. Try the decision on simulated students before real ones."

---

## 2. Two kinds of judges, one story

Pitch so that a UMass judge **recognizes** the problem and an outside judge **understands** it in one sentence. Never assume the room knows what "Worcester" is.

| | Judge who knows UMass | Judge who doesn't |
| --- | --- | --- |
| What they already have | They've stood in the Worcester line at 7 pm. They know the Grill runs out. | Nothing. "Worcester" sounds like a city. |
| What to give them | The **recognition**: "you know this line." | The **scale**: UMass Amherst runs one of the biggest college dining programs in the US; Worcester alone serves on the order of **12,000 meals a day**. |
| What lands | "It's *our* dining hall, with *tonight's* real menu." | "Every cafeteria, hospital and stadium kitchen makes this same bet every day." |

---

## 3. The 3-minute talk

Tell a story, not a tour. The app stays on screen behind you as evidence; the driver touches it only at the three marked moments. Speak in short sentences, one number per beat, and pause after each number.

### 0:00 · The bet (about 30 s)
"Raise your hand if you've waited in the Grill line at Worcester at 7 pm. *(pause)* Now picture the same kitchen at 9 pm, scraping pans of food nobody ate into the trash.

Same night, same kitchen. At three in the afternoon, someone has to decide how much to cook for about three thousand students without knowing who's coming or what they'll want. Guess low, the line backs up. Guess high, it goes in the bin: about seven ounces of waste for every meal swiped."

### 0:30 · Why nobody fixes it (about 25 s)
"The fix is to try something different. But a kitchen can't experiment on three thousand real students; one bad night means hungry people. So the plan rarely changes, and the waste becomes part of the job."

### 0:55 · What we built (about 30 s)
"So we built a place to experiment where nobody goes hungry. Dineamics runs tonight's real menu thousands of times with simulated students, each with their own diet, tastes and patience. They arrive in waves after class, give up on long lines, and find out when a pan is empty.

And because both versions of the night use the same students, the difference you see is the decision, not luck. When the evidence isn't there, it says so: *better, worse, or can't tell.*"

> **Moment 1:** the hall is already playing behind you. Let one "pan empty" alert appear while you say "find out when a pan is empty."

### 1:25 · What it finds (about 35 s)
"Here's what that buys you. Over 39 past nights, cooking the favorites in smaller batches would have thrown away **about 9% less food**.

It also finds problems before they happen: on Thanksgiving dinner this week, the cornbread runs out on **98% of simulated nights**. That's a fix you make on Tuesday, not an apology you make on Wednesday.

And it's honest about trade-offs: our optimizer can cut waste by almost a third, but only with a few more minutes of empty pans, and it shows the manager that trade."

### 2:00 · AI that has a job (about 30 s)
"Managers don't drag sliders; they ask. Meet Sam the Minuteman, our planning agent. Give Sam a goal like *cut waste 20% without any vegetarian dish running out.* Sam checks past nights, proposes a plan, simulates it, reads the result and tries again.

Sam proposes, the simulator decides, and plain code grades the result, not the AI. When a goal can't be met, Sam says so."

> **Moment 2:** type Sam's goal (warmed up beforehand) and let two or three steps scroll by. Don't wait for the end.

### 2:30 · Cheap enough to use every day (about 15 s)
"Planning a week is thousands of simulated nights. One command rents Vultr servers, runs the week in parallel and gives them back: **about three minutes, for about seven cents.**"

### 2:45 · What the kitchen gets (about 15 s)
"At the end, the manager gets the thing they already use: a cook sheet. How much to make, in what batches, and when to start the last one.

Built at UMass, for UMass Dining. **Test it on simulated students before you try it on real ones.**"

> **Moment 3:** the printed cook sheet (Kitchen plan → production plan → Print preview) on screen for the last line.

### Why it matters (keep these in your back pocket)
- **Food:** less of it thrown away every night, measured against the kitchen's own plan.
- **Students:** shorter lines and fewer empty pans at the dishes they came for.
- **Staff:** decisions made at 3 pm with evidence instead of instinct, and a cook sheet they already know how to use.
- **Beyond UMass:** any kitchen that cooks before it knows who's coming (hospitals, stadiums, other campuses) has the same problem. Another dining hall is new data files, not new code.

**If the Wi-Fi dies:** the app runs fully offline from its built-in bundle (`make web`, no backend). The hall, comparisons and the cook sheet all work. Sam needs the API: open Sam's **History** panel (saved in the browser) and tell the story from a past run.

---

## 4. Track by track: what that judge needs to hear

### [HackUMass] Best UMass Hack
- **It's built on UMass, not just at UMass.** Real Worcester menus (Nov 13–20, 2026, from umassdining.com), the real floor plan and stations (Grill, Wok, Noodles, Sushi, Street Food, Latin…), dinner 4:30–9 and late night 9–midnight, diet shares from the UMass Dining survey.
- **Team knowledge built in:** a Worcester regular on the team confirmed the 7–8 pm peak, 30–40 person lines, Sushi made to order, Street Food pre-cooked.
- **Sam the Minuteman** is the agent's persona: our own tricorn-hat avatar in maroon (not the official logo art).
- **Thanksgiving dinner (Nov 18)** is in the data, and it's the night with the worst stockout of the week (Southern Cornbread).
- **The path to real use is short:** we wrote down exactly which data we'd ask UMass Dining for (requests R01–R08: swipe counts, production logs, waste weights). UMass Dining already measures waste campus-wide (about **7.2 oz per swipe** in FY2026), so the data exists.
- **It generalizes:** "Worcester today; Franklin, Hampshire and Berkshire next." The halls differ only in data files, not code.

### [MLH] Best Use of Gemini API
Gemini is in six places, and **never inside the simulation itself**: it proposes, the engine computes.
1. **Function calling for plain English** (the headline): 13 declared functions, one per setting (`set_servers`, `scale_batch`, `remove_dish`, `set_shift`…). Station and dish arguments are **enums of that night's real ids**, so Gemini can't invent a dish. Invalid calls are sent back with the error for one re-ask; then a JSON-schema fallback; then keywords. About 1–1.3 s per question.
2. **Sam's agent loop**: Gemini function calling over the simulator and the database tools.
3. **Student stories**: click any simulated student and Gemini narrates their night from the actual event trace, then a check verifies the times and numbers it wrote.
4. **"Why?" explanations** of a result.
5. **New-dish estimate**: add a dish that has never been served; Gemini estimates how much each type of student would like it, shown as numbers you can edit, labeled "AI estimate".
6. **Similar dishes** via embeddings.
- **The line:** "Gemini turns intent into the simulator's own function calls. Every number you see came from the engine, and every AI output is checked and has a fallback."

### [MLH] Best Use of Vultr
- **Production deploy:** app + API on a Vultr Ubuntu server, HTTPS via Caddy, systemd service, one-command setup script (`deploy/setup.sh`), public-site safety (admin token, AI rate limit).
- **Compute is the point, not hosting:** each decision is 30 paired nights; an uncertainty sweep is 1,000 "plausible worlds"; the week sweep was 4,000 nights. The 16-core Vultr server ran 320 new nights in about 14 s of simulation.
- **Elastic fleet** (`dcsim fleet`): one command starts temporary **16-vCPU CPU-optimized Vultr servers** (fresh Ubuntu 24.04, set up over SSH in about 60 s), runs the plan optimizer on all menu dates in parallel, saves every search to the database, and **deletes every server** at the end, on error, on Ctrl-C, or after a time limit. A live Fleet card shows each server's progress.
- **First fleet run (measured):** all 8 dates × 80 plans in **3.3 min on 3 servers, about \$0.07**, every server deleted. One after another on those servers: 6.0 min; on the 8-core laptop: about 13 min. We asked for 8 servers; the account's limit allowed 3, so the 3 shared the dates (with 8: about 2 min).
- **The line:** "Infrastructure stopped being the bottleneck: a week of plan searches runs in parallel on Vultr in the time one date takes on a laptop."
- ⚠️ Fill in before judging: the timed `dcsim worlds --worlds 1000` on the server (`deploy/README.md`). Quote only numbers you measured.

### [Sentient OS] Best Agentic Hack
Sam is a real agent, not a chatbot with a button:
- **Goal → plan → act → observe → adjust loop.** Gemini parses the goal into targets, proposes settings, calls `run_simulation`, reads a factual diagnosis, adjusts, tries again (**max 5 tries**).
- **Tools:** the simulator, the setting functions (batches, servers, shifts, cooks, presets, remove a dish), and the **Tiger Data database through an MCP server** (`tiger mcp start`) for past nights: up to 6 read-only SQL lookups per run, each guarded (one SELECT only, read-only transaction, 20 s timeout, 40 rows).
- **Guardrails a judge will like:** the goal check is **deterministic code**. Sam may not "cheat" by assuming fewer students (crowd, arrival and popularity settings are hidden while planning). It can't remove a protected dish. It's told staff cost money. Its final plan is **re-validated on 30 unseen nights**.
- **Honesty is a feature:** on "cut waste 20% without any vegetarian dish running out", Sam's best try cut waste about 18–22% and protected every vegetarian dish, but missed meals came out slightly worse, so it reports **"closest plan, goal not fully met."** Say this on stage; it's more convincing than a fake win.
- **Three modes in one box:** a goal (plans), a what-if ("what if we add a Grill server and it's 10% busier?"), or a question answered from the database ("which dishes ran out on rainy Fridays?").
- **The line:** "Sam proposes, the engine decides, and code grades the homework."
- ⚠️ We couldn't find whether the Sentient track requires their own SDK; ask at check-in.

---

## 5. Judging criteria: the proof for each

### Innovation
- Most "AI + cafeteria" projects ask an LLM to *imagine* students. We **simulate** them (a discrete-event model in SimPy: lines, pans that empty, expire and get re-cooked, cooks and ovens as limited resources) and use AI only where it's good: language, intent, stories.
- **Paired nights (common random numbers)** give honest verdicts with ranges, including "can't tell". That's rare in a hackathon project and normal in real operations research.
- An agent that **experiments in a simulator** before anyone touches real food.

### Functionality
- It's live: open the link on your phone during the demo.
- **8 real menu dates**, dinner + late night. 5 presets, an optimizer (80 plans, waste vs. stockout trade-off), plausible-worlds sweep, similar-night search, Sam, cook sheet with Print/CSV.
- Tested: **220 engine tests, 66 web tests**, browser smoke tests at desktop/tablet/phone widths, a golden-baseline test that fails if anyone changes the numbers by accident.
- Works **offline** from a built-in bundle, so the demo can't be killed by Wi-Fi.

### Practicality
- The output is the thing a kitchen already uses: a **cook sheet** (portions, batch size, reorder point, last batch time).
- Cheap to run: a laptop handles a single decision; cloud bursts only for big sweeps, and those servers delete themselves.
- **Numbers that matter:** on Nov 13 the optimizer finds plans with about **28.6% less simulated waste for about 6 more stockout-minutes a night**, and shows that trade-off instead of hiding it. A back-test of "smaller batches of favorites" over 39 past nights: **about 8.8% less waste (range 5.6–10.4%)**.
- Plugs into real data: the waste history format matches the tools dining programs already use (LeanPath / MetaFoodX-style exports).
- Scales: another hall is new data files, not new code.

### Presentation
- Open with the hand-raise, keep to the 3-minute table, one idea per sentence, **one number per beat**.
- Every simulated number has a range; every made-up number has a "Synthetic" badge. Point at a badge once; judges trust you more.
- Everyone on the team speaks at least once (narrator, driver, Q&A).

---

## 6. Be honest before they ask (say it first)

Say this in one breath during the demo or the first question: **"The menus, hours, floor plan and diet shares are real. The 'recorded' waste history is synthetic, generated by our engine with noise, because there's no public UMass waste data per dish. Everything synthetic is labeled, and we listed the exact data we'd ask UMass Dining for."**

---

## 7. Q&A prep

| Likely question | Answer |
| --- | --- |
| "Is this real data?" | §6. Menus real; waste history synthetic and labeled; data requests R01–R08 written up. |
| "How do you know the simulation is right?" | Calibrated against the team's observations (peak time, line lengths, run-outs), held-out calibration checks, a golden test. Honest limit: it needs UMass swipe and waste data to be validated for real. |
| "Why not just ask an LLM how much to cook?" | An LLM can't count 3,700 students through 12 stations. It's great at turning "one more Grill server" into settings, so that's what we use it for. |
| "What does the agent do that a script can't?" | It turns a fuzzy goal into targets, chooses which levers to try, reads the diagnosis and adapts. The *grading* is a script, on purpose. |
| "What if the goal is impossible?" | Sam says "closest plan, goal not fully met" and shows which check failed. |
| "Why Vultr and not your laptop?" | 30 nights per decision × hundreds of candidate plans × 8 dates. The fleet ran all 8 dates in 3.3 min instead of about 13 on a laptop, for about 7 cents, and tears itself down. |
| "Who would use this?" | A dining hall's production manager, the afternoon before service. Output: tonight's cook sheet. |
| "What's next?" | Real swipe and waste data from UMass Dining, the other three dining commons, a weekly planning view. |
| "Cost of the AI?" | Gemini free tier works; answers are cached, and every AI feature has a non-AI fallback. |

---

## 8. Before you walk up (checklist)

- [ ] Open the live link in a clean Chrome window, zoom 110%, bookmarks bar hidden.
- [ ] **Warm the cache:** run the Lab question, the extra-server compare and Sam's goal once, so nothing loads on stage.
- [ ] Turn on ledger writes on the server if you want Adopt to log to Tiger on stage (`DCSIM_LEDGER_WRITES=1`, `deploy/README.md`), and back off after.
- [ ] Have the Nov 18 cornbread result open in a second tab (Tiger dashboard `/tiger` or a terminal with `dcsim tsdb query sweep`).
- [ ] Fill in the Vultr timings (§4) or drop that sentence.
- [ ] Check the current Princeton Review dining ranking before quoting UMass as #1.
- [ ] Offline backup ready: `make web` on the laptop.
- [ ] Phones on silent; one person keeps time.
