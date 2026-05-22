"""FastAPI viewer: paper.json + PDF + figures + streaming define/explain/chat."""
import argparse
import io
import json
import queue
import sys
import tempfile
import threading
import webbrowser
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from config import DATA_DIR
import ai
import figure_explain
import highlights
import run as run_pipeline
import toc_summary
from rag import RagIndex

ROOT = Path(__file__).parent


def _pick_paper_id(arg: str | None) -> str:
    if arg:
        return arg
    if not DATA_DIR.exists():
        print(f"ERROR: no data directory at {DATA_DIR}. Run run.py first.", file=sys.stderr)
        sys.exit(1)
    candidates = [d.name for d in DATA_DIR.iterdir() if d.is_dir()
                  and (d / "paper.json").exists()]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        print(f"ERROR: no processed papers under {DATA_DIR}. Run run.py first.", file=sys.stderr)
        sys.exit(1)
    print(f"ERROR: multiple papers in {DATA_DIR}: {candidates}. Pass one as an arg.",
          file=sys.stderr)
    sys.exit(1)


@dataclass
class PaperContext:
    paper_id: str
    data_dir: Path
    pdf_path: Path
    paper: dict
    rag_index: RagIndex
    toc_roots: list
    toc_cache: dict


def _normalize_figures_shape(paper: dict) -> None:
    """Old paper.json had pages[].figures as ['figures/foo.png', ...]. Wrap into
    the structured shape so the rest of the app can assume dicts. Bbox is null
    for legacy entries (overlays won't render — Ctrl+I no-ops)."""
    for page in paper.get("pages", []) or []:
        figs = page.get("figures") or []
        new_figs = []
        for k, fig in enumerate(figs):
            if isinstance(fig, dict):
                new_figs.append(fig)
                continue
            if isinstance(fig, str):
                stem = Path(fig).stem or f"figure_legacy_{page.get('index', 0):03d}_{k}"
                new_figs.append({
                    "id": stem,
                    "path": fig,
                    "bbox": None,
                    "page_width": None,
                    "page_height": None,
                    "caption": None,
                })
        page["figures"] = new_figs


def _load_context(paper_id: str) -> PaperContext:
    data_dir = DATA_DIR / paper_id
    paper_json_path = data_dir / "paper.json"
    if not paper_json_path.exists():
        raise FileNotFoundError(f"{paper_json_path} not found")
    pdf_path = data_dir / "source.pdf"
    if not pdf_path.exists():
        raise FileNotFoundError(f"{pdf_path} not found")
    paper = json.loads(paper_json_path.read_text(encoding="utf-8"))
    _normalize_figures_shape(paper)
    rag_index = RagIndex(data_dir)
    toc_roots = toc_summary.build_tree(paper.get("toc", []), len(paper.get("pages", [])))
    toc_cache = toc_summary.load_cache(data_dir)
    return PaperContext(
        paper_id=paper_id, data_dir=data_dir, pdf_path=pdf_path,
        paper=paper, rag_index=rag_index, toc_roots=toc_roots, toc_cache=toc_cache,
    )


def _list_papers() -> list[dict]:
    if not DATA_DIR.exists():
        return []
    out = []
    for d in sorted(DATA_DIR.iterdir(), key=lambda p: p.name.lower()):
        if not d.is_dir():
            continue
        pj = d / "paper.json"
        if not pj.exists():
            continue
        title = d.name
        try:
            data = json.loads(pj.read_text(encoding="utf-8"))
            title = data.get("title") or data.get("paper_id") or d.name
        except Exception:
            pass
        out.append({"paper_id": d.name, "title": title})
    return out


def build_app(initial_paper_id: str) -> FastAPI:
    app = FastAPI()
    app.state.current = _load_context(initial_paper_id)
    app.state.swap_lock = threading.Lock()
    app.state.upload_lock = threading.Lock()

    def cur() -> PaperContext:
        ctx = app.state.current
        if ctx is None:
            raise HTTPException(503, "no paper loaded")
        return ctx

    @app.get("/")
    def index(paper: str | None = None) -> FileResponse:
        if paper:
            target = DATA_DIR / paper / "paper.json"
            if target.exists():
                with app.state.swap_lock:
                    try:
                        app.state.current = _load_context(paper)
                    except Exception as e:
                        print(f"WARN: failed to swap to '{paper}': {e}", file=sys.stderr)
        return FileResponse(ROOT / "viewer.html")

    NO_STORE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}

    @app.get("/figures/{name:path}")
    def figures(name: str) -> FileResponse:
        ctx = cur()
        fp = ctx.data_dir / "figures" / name
        if not fp.exists():
            raise HTTPException(404, "figure not found")
        return FileResponse(fp, headers=NO_STORE)

    @app.get("/api/paper")
    def api_paper() -> Any:
        from fastapi.responses import JSONResponse
        return JSONResponse(cur().paper, headers=NO_STORE)

    @app.get("/api/pdf")
    def api_pdf() -> FileResponse:
        return FileResponse(cur().pdf_path, media_type="application/pdf", headers=NO_STORE)

    @app.get("/api/papers")
    def api_papers() -> Any:
        from fastapi.responses import JSONResponse
        return JSONResponse({"papers": _list_papers(), "current": cur().paper_id}, headers=NO_STORE)

    class DefineBody(BaseModel):
        word: str
        before: str = ""
        after: str = ""

    class ExplainBody(BaseModel):
        sentence: str
        before: str = ""
        after: str = ""

    class ChatBody(BaseModel):
        message: str
        history: list[dict] = []
        scope: str = "paper"
        page_index: int | None = None

    @app.post("/api/define")
    def api_define(body: DefineBody) -> StreamingResponse:
        if not body.word.strip():
            raise HTTPException(400, "word is empty")
        return StreamingResponse(
            ai.define_stream(body.word, body.before, body.after),
            media_type="text/plain; charset=utf-8",
        )

    @app.post("/api/explain")
    def api_explain(body: ExplainBody) -> StreamingResponse:
        if not body.sentence.strip():
            raise HTTPException(400, "sentence is empty")
        return StreamingResponse(
            ai.explain_stream(body.sentence, body.before, body.after),
            media_type="text/plain; charset=utf-8",
        )

    @app.post("/api/chat")
    def api_chat(body: ChatBody) -> StreamingResponse:
        ctx = cur()
        if not body.message.strip():
            raise HTTPException(400, "message is empty")
        scope = body.scope if body.scope in ("paper", "page") else "paper"
        page_md = None
        if scope == "page":
            if body.page_index is None or not (0 <= body.page_index < len(ctx.paper["pages"])):
                raise HTTPException(400, "page_index out of range")
            page_md = ctx.paper["pages"][body.page_index]["markdown"]
        return StreamingResponse(
            ai.chat_stream(
                ctx.rag_index, body.message, body.history, scope, body.page_index, page_md
            ),
            media_type="text/plain; charset=utf-8",
        )

    class FigureExplainBody(BaseModel):
        figure_id: str | None = None
        figure_ref: str | None = None

    @app.post("/api/figure-explain")
    def api_figure_explain(body: FigureExplainBody) -> StreamingResponse:
        ctx = cur()
        fid = (body.figure_id or "").strip()
        ref = (body.figure_ref or "").strip()
        if fid:
            gen = figure_explain.stream(ctx, fid)
        elif ref:
            gen = figure_explain.stream_by_ref(ctx, ref)
        else:
            raise HTTPException(400, "figure_id or figure_ref required")
        return StreamingResponse(gen, media_type="text/plain; charset=utf-8")

    class TocNodeBody(BaseModel):
        anchor: str

    @app.get("/api/toc-summaries")
    def api_toc_summaries() -> dict:
        ctx = cur()
        return {"summaries": ctx.toc_cache, "total": _count_nodes(ctx.toc_roots)}

    @app.post("/api/toc-summarize-all")
    def api_toc_summarize_all() -> StreamingResponse:
        ctx = cur()
        def gen():
            for rec in toc_summary.summarize_all(
                ctx.toc_roots, ctx.paper["pages"], ctx.toc_cache, ctx.data_dir
            ):
                yield json.dumps(rec, ensure_ascii=False) + "\n"
        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.post("/api/toc-summarize-node")
    def api_toc_summarize_node(body: TocNodeBody) -> StreamingResponse:
        ctx = cur()
        node = toc_summary.find_node_by_anchor(ctx.toc_roots, body.anchor)
        if node is None:
            raise HTTPException(404, f"unknown anchor: {body.anchor}")

        def gen():
            for rec in toc_summary.summarize_node(
                node, ctx.paper["pages"], ctx.toc_cache, ctx.data_dir
            ):
                yield json.dumps(rec, ensure_ascii=False) + "\n"
        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.get("/api/highlights")
    def api_highlights() -> Any:
        from fastapi.responses import JSONResponse
        ctx = cur()
        cache = highlights.load_cache(ctx.data_dir)
        return JSONResponse(
            {"highlights": cache, "total": len(ctx.paper["pages"])},
            headers=NO_STORE,
        )

    @app.post("/api/highlight-all")
    def api_highlight_all() -> StreamingResponse:
        ctx = cur()
        cache = highlights.load_cache(ctx.data_dir)
        def gen():
            for rec in highlights.highlight_all(ctx.paper["pages"], cache, ctx.data_dir):
                yield json.dumps(rec, ensure_ascii=False) + "\n"
        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.post("/api/reprocess")
    def api_reprocess() -> StreamingResponse:
        """Re-run the extraction pipeline on the current paper's source.pdf with
        force=True, then hot-swap the in-memory context. Streams NDJSON progress."""
        if not app.state.upload_lock.acquire(blocking=False):
            raise HTTPException(409, "another reprocess/upload is already in progress")
        ctx = cur()
        paper_id = ctx.paper_id
        pdf_path = ctx.pdf_path

        q: queue.Queue = queue.Queue()

        class _Tee(io.TextIOBase):
            def __init__(self) -> None:
                self._buf = ""

            def write(self, s: str) -> int:
                self._buf += s
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if line.strip():
                        q.put({"stage": "log", "msg": line})
                return len(s)

            def flush(self) -> None:
                if self._buf.strip():
                    q.put({"stage": "log", "msg": self._buf})
                    self._buf = ""

        def worker():
            err = None
            try:
                tee = _Tee()
                with redirect_stdout(tee), redirect_stderr(tee):
                    run_pipeline.run(str(pdf_path), force=True)
                tee.flush()
            except SystemExit as e:
                err = f"pipeline exited: code={e.code}"
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
            finally:
                q.put({"sentinel": True, "error": err})

        def gen():
            t = threading.Thread(target=worker, daemon=True)
            t.start()
            yield json.dumps({"stage": "start", "msg": f"reprocessing {paper_id} (force=True)"}) + "\n"
            try:
                while True:
                    rec = q.get()
                    if isinstance(rec, dict) and rec.get("sentinel"):
                        err = rec.get("error")
                        if err:
                            yield json.dumps({"stage": "error", "msg": err}) + "\n"
                            yield json.dumps({"done": True, "ok": False}) + "\n"
                        else:
                            try:
                                with app.state.swap_lock:
                                    app.state.current = _load_context(paper_id)
                                yield json.dumps({"done": True, "ok": True, "paper_id": paper_id}) + "\n"
                            except Exception as e:
                                yield json.dumps({"stage": "error", "msg": f"reload failed: {e}"}) + "\n"
                                yield json.dumps({"done": True, "ok": False}) + "\n"
                        break
                    yield json.dumps(rec, ensure_ascii=False) + "\n"
            finally:
                app.state.upload_lock.release()

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.post("/api/upload-pdf")
    async def api_upload_pdf(file: UploadFile = File(...)) -> StreamingResponse:
        if not app.state.upload_lock.acquire(blocking=False):
            raise HTTPException(409, "another upload is already in progress")
        try:
            raw = await file.read()
            orig_name = file.filename or "uploaded.pdf"
            if not orig_name.lower().endswith(".pdf"):
                app.state.upload_lock.release()
                raise HTTPException(400, "only .pdf files are accepted")
            stem = Path(orig_name).stem or "uploaded"
            tmp_dir = Path(tempfile.mkdtemp(prefix="pdfupload_"))
            tmp_path = tmp_dir / f"{stem}.pdf"
            tmp_path.write_bytes(raw)
            paper_id = stem.lower().replace(" ", "_")
        except HTTPException:
            raise
        except Exception:
            app.state.upload_lock.release()
            raise

        q: queue.Queue = queue.Queue()
        SENTINEL = object()

        class _Tee(io.TextIOBase):
            def __init__(self, stream_name: str) -> None:
                self.stream_name = stream_name
                self._buf = ""

            def write(self, s: str) -> int:
                self._buf += s
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if line.strip():
                        q.put({"stage": self.stream_name, "msg": line})
                return len(s)

            def flush(self) -> None:
                if self._buf.strip():
                    q.put({"stage": self.stream_name, "msg": self._buf})
                    self._buf = ""

        def worker():
            err = None
            try:
                out_tee = _Tee("log")
                err_tee = _Tee("log")
                with redirect_stdout(out_tee), redirect_stderr(err_tee):
                    run_pipeline.run(str(tmp_path), force=False)
                out_tee.flush(); err_tee.flush()
            except SystemExit as e:
                err = f"pipeline exited: code={e.code}"
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
            finally:
                q.put({"sentinel": True, "error": err})

        def gen():
            t = threading.Thread(target=worker, daemon=True)
            t.start()
            yield json.dumps({"stage": "start", "msg": f"processing {orig_name} -> {paper_id}"}) + "\n"
            try:
                while True:
                    rec = q.get()
                    if isinstance(rec, dict) and rec.get("sentinel"):
                        err = rec.get("error")
                        if err:
                            yield json.dumps({"stage": "error", "msg": err}) + "\n"
                            yield json.dumps({"done": True, "ok": False}) + "\n"
                        else:
                            try:
                                with app.state.swap_lock:
                                    app.state.current = _load_context(paper_id)
                                yield json.dumps({"done": True, "ok": True, "paper_id": paper_id}) + "\n"
                            except Exception as e:
                                yield json.dumps({"stage": "error", "msg": f"load failed: {e}"}) + "\n"
                                yield json.dumps({"done": True, "ok": False}) + "\n"
                        break
                    yield json.dumps(rec, ensure_ascii=False) + "\n"
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                    tmp_path.parent.rmdir()
                except Exception:
                    pass
                app.state.upload_lock.release()

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    return app


def _count_nodes(roots) -> int:
    n = 0
    def walk(node):
        nonlocal n
        n += 1
        for c in node.children:
            walk(c)
    for r in roots:
        walk(r)
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="Browser viewer for a processed paper")
    parser.add_argument("paper_id", nargs="?", help="Folder under data/ (default: only one)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    paper_id = _pick_paper_id(args.paper_id)
    app = build_app(paper_id)

    if not args.no_open:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()

    print(f"Serving '{paper_id}' at http://127.0.0.1:{args.port}", file=sys.stderr)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
