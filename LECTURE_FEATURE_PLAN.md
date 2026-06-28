# Lecture-Page Generation — Implementation Plan

## Goal
After section summaries exist, a **"Generate Lecture"** button builds a
self-contained, multi-depth lecture HTML for the current paper (the format
proven in `claude_code_lectures.html`), reusing the existing summary/figure
data and the project's resumable-batch + NDJSON-progress patterns.

## Key insight: most of it is *not* LLM work
| Part | Source | LLM? |
|---|---|---|
| Modules + page ranges + stage order | `toc_summary.build_tree(toc, num_pages)` — already exists | No |
| Figure placement (+ captions) | `paper.json` `pages[].figures` (id/caption) + `find_node_for_page()` | No |
| **Summary** depth | seed from `toc_summaries.json` (parent summary + child bullets) | Free / optional polish |
| `terms` chips | reuse `highlights.py` per-page key-term cache | No |
| Expanded + Distillation + tagline + hero | per-chapter `markdown` → **one structured call per module** | Yes |

So a 12-chapter paper ≈ **12 LLM calls** (one `chat_json` per top-level chapter
returning all depths at once), not 36 — and the rest is glue over data you've
already paid for.

## Backend

### New module: `lecture.py` (mirrors `toc_summary.py`)
- `build_modules(toc_roots, pages, toc_cache)` → list of module skeletons from
  **level-1 nodes**; each carries its page range, child-summary bullets (from
  `toc_cache`), and the figures whose page falls in its range.
- `generate_module(node, pages, toc_cache, cache, data_dir)` — one
  `llm_client.chat_json(system, user)` call returning
  `{tagline, expanded, distillation, summary, key_points, terms}`.
  Input = capped chapter markdown (reuse `_MAX_LEAF_CHARS`) + child bullets.
  Robust to messy local-LLM output via existing `_extract_json`.
- `generate_all(...)` — **post-order, resumable, skip-good, retry-failed**,
  `save_cache` after each module, `llm_client.llm_sleep()` between calls — the
  exact contract of `summarize_node`. Cache: `data/<paper_id>/lecture/modules.json`.
- `render_html(modules, meta)` — inject `MODULES` JSON + hero into
  `lecture_template.html`; figures referenced as `/figures/<name>` so they load
  when served in-app. Write `data/<paper_id>/lecture/lecture.html`.
- Hero/thesis: one extra `chat` call over the abstract (page 1) for a one-line
  thesis + lede; fall back to the root summary if it errors.

### New prompts (match `prompts/*.system.txt` style, "easy English")
- `lecture_module.system.txt` — emit the per-module JSON, three depths, faithful
  to source, omit `key_points` when no concrete structure exists (so bio/math
  papers don't get fake pseudocode).
- `lecture_hero.system.txt` — one-line thesis + lede from the abstract.

### New endpoints in `serve.py` (mirror graphrag-build / toc-summarize-all)
- `GET  /api/lecture` → built `lecture.html` (FileResponse) or 404.
- `GET  /api/lecture-status` → `{built, total, done, failed}`.
- `POST /api/lecture-build` → `StreamingResponse` NDJSON, one record per module
  (`{anchor, title, ok, error?}`), re-render HTML at the end. Re-clicking only
  retries missing/failed modules (skip-good), like `toc-summarize-all`.

### Template: `lecture_template.html`
The `claude_code_lectures.html` chrome with the `MODULES=[...]` array and hero
replaced by `/*__MODULES__*/` and `/*__HERO__*/` placeholders. Make the
`key_points`/structure block and figures optional in the render (already
data-driven). Generic stage handling: either drop stages or cluster top-level
chapters into 3–5 phases via one optional LLM call.

## Frontend (`viewer.html`)
- A **"Generate Lecture"** button near the TOC-summary controls, enabled once
  summaries exist. Reuse the NDJSON read loop (`getReader`/`TextDecoder`,
  e.g. lines 1321, 3236) to show `done/total` progress and per-module chips.
- On finish, open `/api/lecture` in a new tab (or an in-app panel).
- **Per-module retry chip** on failures (mirrors the per-node TOC retry at
  line 1288) → `POST /api/lecture-build` is resumable, so it just re-runs failed.

## Why this respects the constraints
- **Flaky local LLM** → resumable, per-module, manual-retry; no auto-retry/knobs
  ([[prefer-manual-retry-no-config-knobs]], [[llm-instability-is-local-server]]).
- **Progressive feel** → Summary depth renders from cache immediately; Expanded/
  Distillation fill in as modules complete.
- **Faithfulness** → each module links back to its PDF pages ("jump to source");
  figure captions used verbatim.

## Verification
1. Build on `claude_code.pdf` → compare against the hand-written reference.
2. Build on a non-CS paper (`alpha_fold2.pdf`) → confirm `key_points` is omitted
   gracefully and figures/depths still render.
3. Kill the LLM mid-build → re-click → only failed modules regenerate.
4. Playwright smoke test: nav count, depth switch, figures load, no console errors.

## Open choices (need your call)
- **Stages**: keep stage-grouping (extra clustering call) or drop for generality?
- **Mermaid per module**: emit an optional chapter flow-diagram (you already have
  mermaid + lightbox)? Great for method papers, skippable otherwise.
- **Standalone export**: add a "Download portable .html" that base64-inlines figures?
