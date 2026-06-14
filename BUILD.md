# Build: standalone `paper_reader.exe`

How to package the FastAPI paper viewer ([serve.py](serve.py)) into a single Windows
executable that runs without Python or a venv.

The build uses **PyInstaller** in one-file mode. `viewer.html`, `prompts/`, `env.txt`,
and `data/` are **not** embedded — they stay editable beside the exe.

## Prerequisites

- Windows (the exe is OS + CPU-arch specific — build on the target platform).
- The project venv at `..\..\.venv\` (one level up at `1_Understanding_fast\.venv`)
  with dependencies installed (`requirements.txt`).
- PyInstaller installed into that venv:

  ```powershell
  ..\..\.venv\Scripts\pip install pyinstaller
  ```

## Build steps

1. From the project root (`paper_read_project_pdf\`), run PyInstaller with the spec:

   ```powershell
   ..\..\.venv\Scripts\python.exe -m PyInstaller paper_reader.spec
   ```

   Output: `dist\paper_reader.exe`.

   To rebuild from scratch (after changing the spec or major code changes):

   ```powershell
   Remove-Item -Recurse -Force build, dist
   ..\..\.venv\Scripts\python.exe -m PyInstaller paper_reader.spec
   ```

2. Copy the runtime files beside the exe:

   ```powershell
   Copy-Item env.txt dist\
   Copy-Item viewer.html dist\
   Copy-Item -Recurse prompts dist\
   Copy-Item -Recurse graphrag_template dist\   # GraphRAG settings/prompts template (editable)
   ```

3. Copy your processed papers folder beside the exe:

   ```powershell
   Copy-Item -Recurse data dist\
   ```

4. The distributable `dist\` folder looks like:

   ```
   dist/
   ├── paper_reader.exe   the executable
   ├── env.txt            API key / model config (edit this)
   ├── viewer.html        the browser UI
   ├── prompts/           prompt templates (edit these)
   ├── graphrag_template/ GraphRAG settings + index/query prompts (copied per paper on first build)
   └── data/              processed papers (paper.json, source.pdf, figures/, graphrag/)
   ```

## Run

```powershell
# opens browser at http://127.0.0.1:8000
.\dist\paper_reader.exe

# custom port, no auto-open
.\dist\paper_reader.exe --port 8099 --no-open

# open a specific paper by folder name under data/
.\dist\paper_reader.exe attentionisallyouneed
```

Verify it works:

```powershell
curl http://127.0.0.1:8000/api/health   # -> {"status":"ok"}
```

## Distribute

Ship the **entire `dist\` folder**. The recipient edits `env.txt` with their own
`OPENAI_API_KEY` and runs `paper_reader.exe`. No Python install required.

## Why files stay external

[config.py](config.py) and [serve.py](serve.py) detect frozen mode (`sys.frozen`) and
load files from the **exe's directory** (`Path(sys.executable).parent`):

- `env.txt` — keeps API keys **out of the binary** and editable.
- `prompts/` — lets you tweak prompt wording **without rebuilding**.
- `viewer.html` — the browser UI; editable for quick front-end tweaks.
- `data/` — processed papers; grows at runtime and must stay writable.

## What's in the spec

[paper_reader.spec](paper_reader.spec) declares:

- **Entry point**: `serve.py`
- **Hidden imports**: all local modules (`ai`, `config`, `figure_explain`,
  `highlights`, `run`, `toc_summary`, `toc`, `rag`, `graph`, `wiki`, `extract`,
  `pipeline`, `llm_client`, plus `agentic_rag`, `graphrag_manager`, `graphrag_qa.*`)
  plus all `uvicorn` submodules (dynamically loaded at startup).
- **One-file mode**: everything packed into a single `.exe`.

### Agentic GraphRAG bundling (whole-paper chat)

Whole-paper chat runs Microsoft **GraphRAG 3.1.0** in-process. GraphRAG is a
multi-package namespace install with native deps, so the spec `collect_all`s the full
stack (`graphrag`, `graphrag_cache/chunking/common/input/llm/storage/vectors`,
`lancedb`, `pyarrow`, `litellm`, `graspologic_native`, `spacy`/`thinc`/`blis`,
`networkx`, …) and `copy_metadata`s the packages that read their own version at runtime.

- **tiktoken cache**: GraphRAG tokenizes with `o200k_base`, which tiktoken normally
  *downloads* on first use — fatal for an offline exe. The repo ships a pre-warmed
  `tiktoken_cache/` (bundled into the exe and pointed at by the `rthook_graphrag.py`
  runtime hook via `TIKTOKEN_CACHE_DIR`). Regenerate it after a tiktoken upgrade with:

  ```powershell
  $env:TIKTOKEN_CACHE_DIR="$PWD\tiktoken_cache"
  ..\..\.venv\Scripts\python -c "import tiktoken; [tiktoken.get_encoding(e).encode('x') for e in ('o200k_base','cl100k_base')]"
  ```

- **First-run index build**: opening a paper kicks off a one-time GraphRAG index build
  in the background (`data/<paper_id>/graphrag/`). Whole-paper chat falls back to vector
  search until it finishes; the chat header shows an **Indexing… / Graph ready / Index
  failed** pill and a **Rebuild index** button (`/api/graphrag-build`). Builds make many
  LLM calls — on a flaky/self-hosted LLM they can partially fail; press **Rebuild index**
  to retry.

## Troubleshooting

- **`ModuleNotFoundError` at startup** — a dynamically-imported module was missed;
  add it to `hiddenimports` in `paper_reader.spec` and rebuild.
- **`viewer.html` not found** — ensure `viewer.html` sits next to the exe (step 2).
- **`env.txt` not found / API errors** — ensure `env.txt` sits next to the exe and
  contains a valid `OPENAI_API_KEY`.
- **No papers available** — ensure `data/` with at least one processed paper
  (`data/<paper_id>/paper.json`) sits next to the exe (step 3).
- **Antivirus flags the exe** — PyInstaller one-file binaries are sometimes
  false-positived; the `build\` artifacts and source remain available for inspection.
- **GraphRAG `PackageNotFoundError` / `importlib.metadata` errors** — a package's
  dist-info wasn't bundled; add it to the `copy_metadata(...)` loop in the spec.
- **tiktoken tries to download / `Connection error` during whole-paper chat** — the
  bundled `tiktoken_cache/` is missing or stale; regenerate it (above) and rebuild, or
  drop a `tiktoken_cache/` folder next to the exe (the runtime hook picks it up).
- **`lancedb`/`pyarrow` native import error in the exe** — ensure those packages were
  installed in the build venv before packaging; `collect_all` only bundles what's
  installed.
