# LLM drafts (not engine inputs)

Nothing in `engine/` reads this folder. It keeps LLM output that was reviewed and **not** adopted.

- `rejected/2026-11-12_personas_raw_qwen3.8-27b.json`: persona set from Groq `qwen/qwen3.8-27b`.
  Rejected: near-duplicate personas ("Late Hearty Eater" x4, "Early Light Eater" x4), the vegetarian
  batch came back as broken JSON, two empty personas dropped. Marked `incomplete`; `dcsim personas adopt` refuses it.

The engine uses the hand-written `data/worcester/personas.json`. See `LLM.md`.
