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
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from config import DATA_DIR
import ai
import extract
import figure_explain
import highlights
import run as run_pipeline
import pipeline
import rag
import toc_summary
from rag import RagIndex
import graph as graph_module
import wiki as wiki_module

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).parent
else:
    ROOT = Path(__file__).parent
_CACHE_FILE = ROOT / ".last_paper_id"


def _load_cached_paper_id() -> str | None:
    """Load the last used paper_id from cache, if it exists and is valid."""
    if _CACHE_FILE.exists():
        try:
            paper_id = _CACHE_FILE.read_text(encoding="utf-8").strip()
            if paper_id:
                # Verify it still exists
                if (DATA_DIR / paper_id / "paper.json").exists():
                    return paper_id
        except Exception:
            pass
    return None


def _save_cached_paper_id(paper_id: str) -> None:
    """Save the paper_id to cache for next startup."""
    try:
        _CACHE_FILE.write_text(paper_id, encoding="utf-8")
    except Exception:
        pass


def _pick_paper_id(arg: str | None) -> str | None:
    if arg:
        return arg
    if not DATA_DIR.exists():
        return None
    candidates = [d.name for d in sorted(DATA_DIR.iterdir()) if d.is_dir()
                  and (d / "paper.json").exists()]

    # Try cache first
    cached = _load_cached_paper_id()
    if cached and cached in candidates:
        return cached

    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return None

    # Multiple papers: show interactive menu
    print(f"\nFound {len(candidates)} papers. Choose one:", file=sys.stderr)
    for i, paper in enumerate(candidates, 1):
        title = paper
        try:
            paper_json = DATA_DIR / paper / "paper.json"
            data = json.loads(paper_json.read_text(encoding="utf-8"))
            title = data.get("title") or data.get("paper_id") or paper
        except Exception:
            pass
        print(f"  {i}. {title} ({paper})", file=sys.stderr)

    while True:
        try:
            choice = input("\nEnter number (or press Ctrl+C to cancel): ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]
            print(f"Invalid choice. Enter a number between 1 and {len(candidates)}.", file=sys.stderr)
        except ValueError:
            print(f"Invalid input. Enter a number between 1 and {len(candidates)}.", file=sys.stderr)
        except KeyboardInterrupt:
            print("\nCancelled.", file=sys.stderr)
            sys.exit(0)


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


def build_app(initial_paper_id: str | None) -> FastAPI:
    app = FastAPI()
    app.state.current = _load_context(initial_paper_id) if initial_paper_id else None
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
                        _save_cached_paper_id(paper)
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
        paper = cur().paper.copy() if isinstance(cur().paper, dict) else cur().paper
        if isinstance(paper, dict):
            paper["paper_id"] = cur().paper_id
        return JSONResponse(paper, headers=NO_STORE)

    @app.get("/api/pdf")
    def api_pdf() -> FileResponse:
        return FileResponse(cur().pdf_path, media_type="application/pdf", headers=NO_STORE)

    @app.get("/api/papers")
    def api_papers() -> Any:
        from fastapi.responses import JSONResponse
        ctx = app.state.current
        return JSONResponse({"papers": _list_papers(), "current": ctx.paper_id if ctx else None}, headers=NO_STORE)

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

    @app.post("/api/grammar")
    def api_grammar(body: ExplainBody) -> StreamingResponse:
        if not body.sentence.strip():
            raise HTTPException(400, "sentence is empty")
        return StreamingResponse(
            ai.grammar_stream(body.sentence, body.before, body.after),
            media_type="text/plain; charset=utf-8",
        )

    @app.post("/api/paraphrase")
    def api_paraphrase(body: ExplainBody) -> StreamingResponse:
        if not body.sentence.strip():
            raise HTTPException(400, "sentence is empty")
        return StreamingResponse(
            ai.paraphrase_stream(body.sentence, body.before, body.after),
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

    def _stream_job(start_msg: str, job_fn, reload_paper_id: str | None) -> StreamingResponse:
        """Run job_fn() in a worker thread with stdout/stderr tee'd to an NDJSON
        progress stream. On success, hot-swap the in-memory context to
        reload_paper_id (if given). The caller must already hold
        app.state.upload_lock; it is released when the stream finishes."""
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
                    job_fn()
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
            yield json.dumps({"stage": "start", "msg": start_msg}) + "\n"
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
                                if reload_paper_id is not None:
                                    with app.state.swap_lock:
                                        app.state.current = _load_context(reload_paper_id)
                                yield json.dumps({"done": True, "ok": True, "paper_id": reload_paper_id}) + "\n"
                            except Exception as e:
                                yield json.dumps({"stage": "error", "msg": f"reload failed: {e}"}) + "\n"
                                yield json.dumps({"done": True, "ok": False}) + "\n"
                        break
                    yield json.dumps(rec, ensure_ascii=False) + "\n"
            finally:
                app.state.upload_lock.release()

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    class ReprocessBody(BaseModel):
        extract_mode: str | None = None
        ocr_only: bool = False

    @app.post("/api/reprocess")
    def api_reprocess(body: ReprocessBody = ReprocessBody()) -> StreamingResponse:
        """Re-run the extraction pipeline on the current paper's source.pdf with
        force=True, then hot-swap the in-memory context. Streams NDJSON progress."""
        ctx = cur()
        if not app.state.upload_lock.acquire(blocking=False):
            raise HTTPException(409, "another reprocess/upload is already in progress")
        paper_id = ctx.paper_id
        pdf_path = ctx.pdf_path
        extract_mode = body.extract_mode
        ocr_only = bool(body.ocr_only)
        return _stream_job(
            f"reprocessing {paper_id} (force=True)",
            lambda: run_pipeline.run(str(pdf_path), force=True,
                                     extract_mode=extract_mode, ocr_only=ocr_only),
            reload_paper_id=paper_id,
        )

    class SummarizeBody(BaseModel):
        force: bool = False

    @app.post("/api/summarize-pages")
    def api_summarize_pages(body: SummarizeBody = SummarizeBody()) -> StreamingResponse:
        """Generate per-page summaries for the current paper from its existing
        markdown (no re-extraction / no re-OCR), persist to paper.json, then
        hot-swap the in-memory context. Streams NDJSON progress.

        Resumable by default: pages that already have a good summary are skipped,
        so re-running only retries empty/errored pages. Pass force=true to
        re-summarize every page."""
        ctx = cur()
        if not app.state.upload_lock.acquire(blocking=False):
            raise HTTPException(409, "another job is already in progress")
        paper_id = ctx.paper_id
        paper_json_path = ctx.data_dir / "paper.json"
        force = bool(body.force)

        def job():
            paper = json.loads(paper_json_path.read_text(encoding="utf-8"))
            pipeline.summarize_pages(paper.get("pages", []), force=force)
            paper_json_path.write_text(
                json.dumps(paper, ensure_ascii=False, indent=2), encoding="utf-8"
            )

        label = f"summarizing pages for {paper_id}{' (force)' if force else ' (failed only)'}"
        return _stream_job(label, job, reload_paper_id=paper_id)

    @app.post("/api/build-rag")
    def api_build_rag() -> StreamingResponse:
        """Build the RAG embedding index for the current paper from its existing
        markdown (no re-extraction / no re-OCR), then hot-swap the in-memory
        context so chat retrieval works. Streams NDJSON progress."""
        ctx = cur()
        if not app.state.upload_lock.acquire(blocking=False):
            raise HTTPException(409, "another job is already in progress")
        paper_id = ctx.paper_id
        data_dir = ctx.data_dir
        paper_json_path = data_dir / "paper.json"

        def job():
            paper = json.loads(paper_json_path.read_text(encoding="utf-8"))
            rag.build_index(paper.get("pages", []), data_dir)

        return _stream_job(f"building RAG index for {paper_id}", job, reload_paper_id=paper_id)

    @app.post("/api/reocr-failed")
    def api_reocr_failed() -> StreamingResponse:
        """Re-run the vision LLM only on pages that previously fell back to text
        extraction (extract_method == 'text-fallback'), persist to paper.json,
        then hot-swap the in-memory context. Leaves already-good pages and their
        summaries untouched, so it is safe to call repeatedly. Streams NDJSON
        progress."""
        ctx = cur()
        if not app.state.upload_lock.acquire(blocking=False):
            raise HTTPException(409, "another job is already in progress")
        paper_id = ctx.paper_id
        pdf_path = ctx.pdf_path
        paper_json_path = ctx.data_dir / "paper.json"

        def job():
            paper = json.loads(paper_json_path.read_text(encoding="utf-8"))
            fixed = extract.reocr_failed_pages(pdf_path, paper.get("pages", []))
            if fixed:
                paper_json_path.write_text(
                    json.dumps(paper, ensure_ascii=False, indent=2), encoding="utf-8"
                )

        return _stream_job(f"re-OCR failed pages for {paper_id}", job, reload_paper_id=paper_id)

    @app.post("/api/upload-pdf")
    async def api_upload_pdf(
        file: UploadFile = File(...),
        extract_mode: str = Form("text"),
        ocr_only: str = Form(""),
    ) -> StreamingResponse:
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
                    run_pipeline.run(str(tmp_path), force=False,
                                     extract_mode=extract_mode, ocr_only=bool(ocr_only))
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

    # ── Wiki / Graph endpoints ────────────────────────────────────────────────────

    @app.get("/api/wiki/graph")
    def api_wiki_graph() -> Any:
        from fastapi.responses import JSONResponse
        return JSONResponse(graph_module.get_graph(cur().paper_id), headers=NO_STORE)

    @app.get("/api/wiki/pages")
    def api_wiki_pages() -> Any:
        from fastapi.responses import JSONResponse
        return JSONResponse({"pages": wiki_module.list_pages(cur().paper_id)}, headers=NO_STORE)

    @app.get("/api/wiki/page/{name}")
    def api_wiki_page(name: str) -> Any:
        from fastapi.responses import JSONResponse
        content = wiki_module.load_page(cur().paper_id, name)
        if content is None:
            raise HTTPException(404, f"wiki page not found: {name}")
        return JSONResponse(
            {"name": name, "content": wiki_module.resolve_wiki_links(content)},
            headers=NO_STORE
        )

    @app.post("/api/wiki/ingest/{paper_id}")
    def api_wiki_ingest(paper_id: str) -> StreamingResponse:
        """Run LLM extraction + wiki update for a paper. Streams NDJSON progress."""
        pj = DATA_DIR / paper_id / "paper.json"
        if not pj.exists():
            raise HTTPException(404, f"paper not found: {paper_id}")

        def gen():
            try:
                wiki_module.ensure_wiki_dir(paper_id)
                paper_data = json.loads(pj.read_text(encoding="utf-8"))
                pages = paper_data.get("pages", [])
                data_dir = DATA_DIR / paper_id

                # Load toc_summaries if available
                toc_summaries = {}
                toc_summaries_file = data_dir / "toc_summaries.json"
                if toc_summaries_file.exists():
                    try:
                        toc_summaries = json.loads(toc_summaries_file.read_text(encoding="utf-8"))
                    except Exception:
                        yield json.dumps({
                            "stage": "warning",
                            "msg": "toc_summaries.json found but could not be loaded"
                        }) + "\n"
                else:
                    yield json.dumps({
                        "stage": "warning",
                        "msg": "toc_summaries.json not found — running without section summaries. Run TOC summarization first for better results."
                    }) + "\n"

                # Step 1: graph extraction + merge
                yield json.dumps({"stage": "start", "msg": f"ingesting {paper_id}"}) + "\n"
                id_map = {}
                for rec in graph_module.ingest_paper(paper_id, pages, toc_summaries):
                    if rec.get("done"):
                        id_map = rec.get("id_map", {})
                    yield json.dumps(rec, ensure_ascii=False) + "\n"

                # Step 2: wiki page generation for affected nodes
                g = graph_module.load_graph(paper_id)
                rag_indices = {}
                all_papers_data = {}
                toc_summaries_by_paper = {}

                for node in g.get("nodes", []):
                    for pid in node.get("papers", []):
                        if pid not in rag_indices:
                            pd = DATA_DIR / pid
                            if (pd / "rag.json").exists():
                                try:
                                    rag_indices[pid] = RagIndex(pd)
                                except Exception:
                                    pass
                            pjp = pd / "paper.json"
                            if pjp.exists():
                                try:
                                    all_papers_data[pid] = json.loads(pjp.read_text(encoding="utf-8"))
                                except Exception:
                                    pass
                            toc_file = pd / "toc_summaries.json"
                            if toc_file.exists():
                                try:
                                    toc_summaries_by_paper[pid] = json.loads(toc_file.read_text(encoding="utf-8"))
                                except Exception:
                                    pass

                affected = list(id_map.values())
                for rec in wiki_module.update_pages_for_paper(
                    paper_id, affected, g, rag_indices, all_papers_data, toc_summaries_by_paper
                ):
                    yield json.dumps(rec, ensure_ascii=False) + "\n"

                # Step 3: rebuild index + log
                wiki_module.rebuild_index(paper_id, g)
                wiki_module.append_log(
                    paper_id, f"ingest: {paper_id} — {len(affected)} nodes affected"
                )
                yield json.dumps({"done": True, "ok": True, "paper_id": paper_id}) + "\n"
            except Exception as e:
                yield json.dumps({"stage": "error", "msg": str(e)}) + "\n"
                yield json.dumps({"done": True, "ok": False}) + "\n"

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    class WikiQABody(BaseModel):
        question: str
        history: list[dict] = []

    @app.post("/api/wiki/qa")
    def api_wiki_qa(body: WikiQABody) -> StreamingResponse:
        """Answer a question using graph + wiki + RAG context."""
        if not body.question.strip():
            raise HTTPException(400, "question is empty")

        ctx = cur()
        g = graph_module.get_graph(ctx.paper_id)
        rag_indices = {}
        for d in DATA_DIR.iterdir():
            if d.is_dir() and (d / "rag.json").exists():
                try:
                    rag_indices[d.name] = RagIndex(d)
                except Exception:
                    pass

        return StreamingResponse(
            wiki_module.wiki_qa_stream(body.question, g, rag_indices, body.history, ctx.paper_id),
            media_type="text/plain; charset=utf-8",
        )

    @app.post("/api/wiki/page/{name}/regenerate")
    def api_wiki_page_regenerate(name: str) -> Any:
        """Regenerate a specific wiki page."""
        from fastapi.responses import JSONResponse
        g = graph_module.get_graph(cur().paper_id)
        node = next((n for n in g.get("nodes", []) if n["id"] == name), None)
        if node is None:
            raise HTTPException(404, f"node not found: {name}")

        rag_indices = {}
        all_papers_data = {}
        toc_summaries_by_paper = {}
        for pid in node.get("papers", []):
            pd = DATA_DIR / pid
            if (pd / "rag.json").exists():
                try:
                    rag_indices[pid] = RagIndex(pd)
                except Exception:
                    pass
            pjp = pd / "paper.json"
            if pjp.exists():
                try:
                    all_papers_data[pid] = json.loads(pjp.read_text(encoding="utf-8"))
                except Exception:
                    pass
            toc_file = pd / "toc_summaries.json"
            if toc_file.exists():
                try:
                    toc_summaries_by_paper[pid] = json.loads(toc_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

        content = wiki_module.regenerate_page(cur().paper_id, name, g, rag_indices, all_papers_data, toc_summaries_by_paper)
        return JSONResponse(
            {"name": name, "content": wiki_module.resolve_wiki_links(content)},
            headers=NO_STORE
        )

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
    parser.add_argument("paper_id", nargs="?", help="Folder under data/ (default: last used or only one)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    paper_id = _pick_paper_id(args.paper_id)
    if paper_id:
        _save_cached_paper_id(paper_id)
    app = build_app(paper_id)

    if not args.no_open:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()

    label = f"'{paper_id}'" if paper_id else "no paper (upload one via the UI)"
    print(f"Serving {label} at http://127.0.0.1:{args.port}", file=sys.stderr)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
