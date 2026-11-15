# Demo video plan: Dineamics · Worcester

**Length:** 2:30 (hard cap 3:00). The best visuals land before 1:00, because many judges stop watching around 2 minutes.
**Tone:** made by students, quick, a little funny (Sam the Minuteman), every number honest. Keep the ranges and "Synthetic" badges on screen.
**Narrator:** one team member doing voiceover. Short lines, one idea per line.

**Logline:** Every UMass student has stood in the Worcester line at 7 pm and watched the Grill run out. Dineamics lets a kitchen manager replay tonight's real menu, student by student, change one thing, and see whether it helps before dinner opens.

---

## Script

| Time | On screen | Voiceover | Feature shown |
| --- | --- | --- | --- |
| 0:00–0:08 | Phone clip: the Worcester line at about 7 pm, then an empty pan at the Grill. Text: *"What if the kitchen could see tonight before it happens?"* | "7 pm at Worcester. You know this line." | Hook |
| 0:08–0:20 | Hard cut to the isometric hall filling with dots at 60× speed. | "About 3,000 students eat dinner here every night. The kitchen has to guess how much to cook. Too little and the Grill runs out. Too much and it goes in the trash." | Problem |
| 0:20–0:45 | **Simulation** (`/hall`): the dots arrive in waves after class, form lines, sit down. A "Grill pan empty" alert pops up. Click one student and their AI-written story streams in. | "This is tonight's real menu from umassdining.com, played out student by student. Every dot is a student. Here's the 5:15 rush right after classes let out." | Replay, alerts, student story |
| 0:45–1:05 | **Try a change → Compare**: pick "extra Grill server". Show the verdict cards (better / worse / can't tell) with ranges. | "Add a server at the Grill. We don't simulate one night, we simulate 30, with the same students both times, so the difference comes from the decision, not luck." | Paired what-ifs, honest verdicts |
| 1:05–1:25 | **What-If Lab** (`/lab`): type *"one more server at Grill and 10% busier"* → Apply and run. Then **Optimize**: the waste-vs-stockouts chart of 80 plans. | "Or just ask in plain English. Gemini turns the question into the simulator's own settings. Optimize searches 80 kitchen plans and shows the trade-off." | Gemini function calling, optimizer |
| 1:25–1:50 | **Ask Sam** (`/sam`): the Minuteman avatar. Type *"cut waste 20% without any vegetarian dish running out"*. The live steps scroll: Tiger lookup, simulate, check, adjust. Press **Adopt**. | "Meet Sam the Minuteman, our planning agent. Give him a goal. He looks up past nights, tries up to five plans, and a fixed check in code, not the AI, decides whether the goal is met." | Agent, Tiger MCP |
| 1:50–2:05 | Tiger SQL result: Southern Cornbread on Nov 18 (Thanksgiving dinner) runs out on 97.6% of nights. Then **Similar nights**: a rainy Friday. | "We ran 4,000 nights across a week of menus on Tiger Data. The answer comes back in about 115 ms: at Thanksgiving dinner, the cornbread runs out almost every time." | Tiger time series, pgvector search |
| 2:05–2:20 | **Kitchen plan** (`/dashboard`): the production plan, Print. Then **Waste history** showing the next 8 nights. | "In the end the manager gets a cook sheet: how many pans to cook, and when." | Cook sheet, waste outlook |
| 2:20–2:30 | The live URL on the Vultr server, team names, UMass maroon end card: *"Built at HackUMass, for UMass Dining."* | "Dineamics. Test it on simulated students before you try it on real ones." | Live deploy (Vultr) |

---

## Feature checklist (every one appears on screen)

- [ ] Isometric hall replay, speed control, alerts (0:20)
- [ ] AI student story (0:20)
- [ ] 30 paired nights with verdicts and ranges (0:45)
- [ ] Plain English → settings through Gemini function calling (1:05)
- [ ] Optimize trade-off chart (1:05)
- [ ] Ask Sam agent + Adopt (1:25)
- [ ] Tiger Data: 4,000-night query, Similar nights (1:50)
- [ ] Kitchen plan / cook sheet, waste outlook (2:05)
- [ ] Live on Vultr (2:20)

## UMass details to use

- **Worcester DC** by name, plus a nod to the other halls in one line ("Worcester today; Franklin, Hampshire and Berkshire next").
- **The 7–8 pm rush** and the **class-change waves**: students know these from experience.
- **Sam the Minuteman** with the tricorn hat (our own avatar, not the official mascot art).
- **The Thanksgiving dinner on Nov 18** and the cornbread: a menu every student remembers.
- **Late night** after 9 pm, and the Southwest crowd walking over.
- Short B-roll: the walk to Worcester from the Southwest towers, the line out the door, the Grill station. **Ask UMass Dining before filming inside the hall.** Otherwise film outside and use the app's hall for everything inside.
- UMass Dining's #1 ranking from the Princeton Review, as one line on the end card (check the current year's ranking first).

## Production notes

- **Record at 1080p** from the live site in a clean Chrome profile, zoomed to 110%. Hide the bookmarks bar.
- **Warm the cache first.** Run the Lab question, Optimize and Sam once before recording so nothing loads on camera. If Sam is slow, cut while it runs and show a "~20 s later" caption.
- **Use the 2026-11-13 date** for the hall and Lab (the most data), and **Nov 18** for the Tiger beat.
- **Be honest on screen.** Leave the "Synthetic" badge visible on Waste history. If anyone asks: the menus are real, the waste history is generated.
- **Pacing:** one cut every 3–5 seconds. Zoom in on the number you're talking about. Add captions, since many people watch muted.
- **Music:** quiet lo-fi from a royalty-free library, lowered under the voice.
- **Don't** read the UI aloud, show the terminal, or explain the engine internals. Mention SimPy and the seeds in a single caption at most.
