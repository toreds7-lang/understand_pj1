# Paper Read Project — PDF library viewer

OCR-free academic paper reader. Extracts text/tables/figures with PDF libraries
(no vision model), renders the original PDF in the browser with **real text
selection** (PDF.js), and lets you ask questions about the paper via RAG chat.

## Architecture

```
┌─────────────┬────────────────────────┬──────────────┬──────────────────┐
│  TOC        │  extracted markdown    │  RAG chat    │  original PDF    │
│  (fitz/     │  ──────────────────    │  ▢ Whole     │  (PDF.js,        │
│   regex/    │  page summary          │  ▣ This page │   text-          │
│   fonts)    │                        │              │   selectable)    │
└─────────────┴────────────────────────┴──────────────┴──────────────────┘
```

| Stage      | Library                          | Role                                            |
| ---------- | -------------------------------- | ----------------------------------------------- |
| Text+MD    | `pymupdf4llm`                    | Per-page markdown with headings / math / tables |
| Tables     | `pdfplumber`                     | Refines tables when row/col count beats above   |
| Figures    | `PyMuPDF` (`fitz`)               | Extracts embedded raster figures as PNG         |
| TOC tier 1 | `fitz.Document.get_toc()`        | Embedded bookmarks (preferred)                  |
| TOC tier 2 | `pdfplumber` + regex             | Parses physical "Contents" page                 |
| TOC tier 3 | `fitz` font-size heuristic       | Final fallback                                  |
| RAG        | OpenAI `text-embedding-3-small`  | Per-page chunked vector index (cosine)          |
| Chat / Q&A | OpenAI Chat Completions          | Define / Explain / RAG answer (streaming)       |
| Viewer     | FastAPI + PDF.js (CDN ESM)       | 4-column SPA, single `viewer.html`              |

## Setup

The shared venv lives one level up at `..\.venv\`.

```powershell
..\.venv\Scripts\pip install -r requirements.txt
```

Put your OpenAI key in `env.txt`:

```
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o
EMBEDDING_MODEL=text-embedding-3-small
LLM_BASE_URL=
EMBEDDING_BASE_URL=
```

`LLM_BASE_URL` / `EMBEDDING_BASE_URL` are optional — set them to point at a
local vLLM-style endpoint instead of OpenAI.

## Process a PDF

```powershell
..\.venv\Scripts\python run.py attentionisallyouneed.pdf
```

This creates `data/<paper_id>/`:

```
data/attentionisallyouneed/
  source.pdf              # copy of the input PDF, served to PDF.js
  paper.json              # pages + summaries + toc + tables + figure bboxes
  figures/                # extracted figure PNGs
  figure_explanations/    # cached Ctrl+I results, one .md per figure id
  rag.json                # chunk texts + page mapping
  rag.npz                 # L2-normalized embeddings (N × 1536)
```

Flags:
- `--force` — reprocess even if `paper.json` exists.

## Launch the viewer

```powershell
..\.venv\Scripts\python serve.py
```

Auto-opens `http://127.0.0.1:8000` in your browser. If multiple papers are
under `data/`, pass the folder name:

```powershell
..\.venv\Scripts\python serve.py attentionisallyouneed --port 8000 --no-open
```

## Using the UI

### Columns

1. **TOC** — clicking an entry scrolls **both** the markdown column and the
   PDF column to that page.
2. **Markdown + summary** — pymupdf4llm-extracted markdown, then a dashed
   horizontal rule, then the LLM-generated easy-English summary for that page.
3. **RAG chat** — see below.
4. **Original PDF (PDF.js)** — drag-select any text; selection lands on the
   transparent text layer overlaying the canvas, so glyph-level selection
   works exactly like a real PDF reader.

### Keyboard shortcuts

| Shortcut | What it does                                                              |
| -------- | ------------------------------------------------------------------------- |
| `Ctrl+D` | Define the selected word (with 3 sentences of context before/after).       |
| `Ctrl+E` | Explain the selected sentence in easy English.                            |
| Dbl-click figure, then `Ctrl+I` | Explain the selected figure (vision LLM + RAG; cached per figure). `Esc` deselects when no popup is open. |
| `Esc`    | Close the streaming popup.                                                |

Selections work in **both** the markdown column and the original-PDF column.

### Chat scope toggle

Above the chat input there are two buttons:

- **Whole paper** — top-k=5 cosine retrieval over all chunks; the answer cites
  `[page N]` markers.
- **This page** — restricts grounding to a single page. The page number auto-
  follows whichever page is currently at the top of the PDF column (tracked
  via `IntersectionObserver`); the number input next to the button lets you
  override manually (1-indexed).

When `This page` is active, each user message in the transcript shows a
`scoped to page N` chip so prior turns stay interpretable.

## API endpoints

| Method | Path                       | Body / Notes                                                       |
| ------ | -------------------------- | ------------------------------------------------------------------ |
| GET    | `/`                        | Serves `viewer.html`.                                              |
| GET    | `/api/paper`               | The full `paper.json` payload.                                     |
| GET    | `/api/pdf`                 | Original PDF, `application/pdf` (consumed by PDF.js).              |
| GET    | `/figures/<name>.png`      | Static figure images.                                              |
| POST   | `/api/define`              | `{word, before, after}` → streaming `text/plain` markdown.         |
| POST   | `/api/explain`             | `{sentence, before, after}` → streaming `text/plain` markdown.     |
| POST   | `/api/chat`                | `{message, history, scope, page_index}` → streaming markdown.      |
| POST   | `/api/figure-explain`      | `{figure_id}` → streaming markdown; vision-grounded, cached on disk. |

`scope` is `"paper"` (default) or `"page"`. When `"page"`, `page_index` is
required and 0-indexed.

## File layout

```
paper_read_project_pdf/
├── README.md           # this file
├── CLAUDE.md           # project instructions for Claude Code
├── requirements.txt
├── env.txt             # API keys / model overrides
├── config.py           # loads env.txt → typed constants
├── llm_client.py       # OpenAI chat + embeddings (vLLM-compatible)
├── extract.py          # pymupdf4llm + pdfplumber + fitz pipeline
├── toc.py              # 3-tier TOC fallback
├── pipeline.py         # per-page summarization
├── rag.py              # chunk + embed + cosine retrieval
├── ai.py               # define / explain / chat streaming prompts
├── run.py              # CLI: PDF → data/<id>/
├── serve.py            # FastAPI server
└── viewer.html         # single-file 4-column SPA (PDF.js + marked + KaTeX)
```

## Re-processing

Re-running `run.py` without `--force` is a no-op when `paper.json` exists. To
re-extract (e.g. after changing extraction logic):

```powershell
..\.venv\Scripts\python run.py attentionisallyouneed.pdf --force
```

This rewrites everything **except** the copied `source.pdf` (which is
overwritten too if `--force` is set).

## Troubleshooting

- **Selection doesn't highlight in the PDF column** — the textLayer rendering
  failed silently. Open DevTools and check for errors in the `renderTextLayer`
  call; the most common cause is a PDF.js version skew between `pdf.mjs` and
  `pdf.worker.mjs`. Both are pinned to the same version in `viewer.html`.
- **TOC is empty** — the PDF has no bookmarks, no physical "Contents" page,
  AND the font-size heuristic couldn't find headings. Inspect
  `paper.json → toc` directly; you may need a different PDF or a tweak to
  `toc.py` thresholds.
- **`Already processed`** — pass `--force` to `run.py`.
- **Chat returns "excerpts do not contain the answer"** — retrieval missed.
  Try `Whole paper` scope, or rephrase using terms that appear verbatim in
  the paper. If consistently failing, check `rag.json` to confirm chunks
  contain real text (not just figure references).
