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
   └── data/              processed papers (paper.json, source.pdf, figures/)
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
  `pipeline`, `llm_client`) plus all `uvicorn` submodules (dynamically loaded at
  startup).
- **One-file mode**: everything packed into a single `.exe`.

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
