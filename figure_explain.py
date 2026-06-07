"""Vision-grounded figure explanation: RAG + rendered page image → multimodal LLM.

Cached per (paper, figure) on disk at:
    data/<paper_id>/figure_explanations/<figure_id>.md
"""
from __future__ import annotations

import base64
import re
import sys
from pathlib import Path
from typing import Iterator

import fitz

import llm_client
from extract import _find_caption_blocks


_PROMPTS_DIR = (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent) / "prompts"
_FIG_REF_RE = re.compile(r"\b(?:Figure|Fig\.?)\s*(\d+)\b", re.IGNORECASE)
_ID_SAFE_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def cache_path(data_dir: Path, figure_id: str) -> Path:
    return data_dir / "figure_explanations" / f"{figure_id}.md"


def load(data_dir: Path, figure_id: str) -> str | None:
    p = cache_path(data_dir, figure_id)
    if p.exists():
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return None
    return None


def _find_figure(paper: dict, figure_id: str) -> tuple[int, dict] | None:
    for page in paper.get("pages", []):
        for fig in page.get("figures", []) or []:
            if isinstance(fig, dict) and fig.get("id") == figure_id:
                return page["index"], fig
    return None


def _caption_figure_number(caption: str | None) -> str | None:
    if not caption:
        return None
    m = _FIG_REF_RE.search(caption)
    return m.group(1) if m else None


def _collect_context(rag_index, caption: str | None, page_index: int) -> str:
    snippets: list[dict] = []
    seen: set[tuple[int, str]] = set()

    def add(rec: dict) -> None:
        key = (rec.get("page", -1), (rec.get("text") or "")[:80])
        if key in seen:
            return
        seen.add(key)
        snippets.append(rec)

    fig_no = _caption_figure_number(caption)
    if fig_no and rag_index.chunks:
        needle = re.compile(rf"\b(?:Figure|Fig\.?)\s*{fig_no}\b", re.IGNORECASE)
        for c in rag_index.chunks:
            if needle.search(c.get("text", "")):
                add(c)
                if len(snippets) >= 4:
                    break

    query = caption or f"Figure on page {page_index + 1}"
    try:
        for hit in rag_index.topk(query, k=5):
            add(hit)
            if len(snippets) >= 6:
                break
    except Exception as exc:
        print(f"[figure_explain] topk failed: {exc}", file=sys.stderr)

    if not snippets:
        return "(no relevant excerpts found)"
    parts = []
    for s in snippets[:6]:
        page = s.get("page", -1)
        text = (s.get("text") or "").strip()
        parts.append(f"[page {page + 1}]\n{text}")
    return "\n\n---\n\n".join(parts)


def _render_page_png_b64(pdf_path: Path, page_index: int, zoom: float = 2.0) -> str:
    doc = fitz.open(str(pdf_path))
    try:
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        png = pix.tobytes("png")
    finally:
        doc.close()
    return base64.b64encode(png).decode("ascii")


_REF_RE = re.compile(r"\b(?:figure|fig\.?)\s*(\d+)\b", re.IGNORECASE)


def _parse_figure_ref(text: str) -> int | None:
    if not text:
        return None
    m = _REF_RE.search(text)
    return int(m.group(1)) if m else None


def _find_figure_by_number(paper: dict, n: int) -> tuple[int, dict] | None:
    target = str(n)
    for page in paper.get("pages", []):
        for fig in page.get("figures", []) or []:
            if isinstance(fig, dict) and str(fig.get("figure_number") or "") == target:
                return page["index"], fig
    return None


def _render_region_png_b64(pdf_path: Path, page_index: int, bbox, zoom: float = 2.0) -> str:
    doc = fitz.open(str(pdf_path))
    try:
        page = doc.load_page(page_index)
        clip = fitz.Rect(*bbox)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
        png = pix.tobytes("png")
    finally:
        doc.close()
    return base64.b64encode(png).decode("ascii")


def _find_caption_in_pdf(pdf_path: Path, n: int) -> tuple[int, str, tuple[float, float, float, float], float, float] | None:
    """Scan the PDF for a 'Figure N' caption block. Returns (page_index, caption, bbox, page_w, page_h)."""
    target = str(n)
    doc = fitz.open(str(pdf_path))
    try:
        for i, page in enumerate(doc):
            for num, text, bbox in _find_caption_blocks(page):
                if num == target:
                    return i, text, bbox, float(page.rect.width), float(page.rect.height)
    finally:
        doc.close()
    return None


def stream_by_ref(ctx, ref_text: str) -> Iterator[str]:
    """Resolve 'figure N' text to a figure and stream an explanation.
    Falls back to caption-search + on-the-fly region render when the figure
    isn't in paper.json."""
    n = _parse_figure_ref(ref_text or "")
    if n is None:
        yield f"Could not parse figure reference: {ref_text!r}"
        return

    found = _find_figure_by_number(ctx.paper, n)
    if found is not None:
        _, fig = found
        yield from stream(ctx, fig["id"])
        return

    synthetic_id = f"figure_num_{n}"
    cached = load(ctx.data_dir, synthetic_id)
    if cached:
        yield cached
        return

    located = _find_caption_in_pdf(ctx.pdf_path, n)
    if located is None:
        yield f"Figure {n} not found in PDF."
        return
    page_index, caption, cap_bbox, pw, ph = located
    cx0, cy0, cx1, cy1 = cap_bbox
    crop = (
        0.0,
        max(0.0, cy0 - 500.0),
        pw,
        min(ph, cy1 + 10.0),
    )

    context = _collect_context(ctx.rag_index, caption, page_index)
    try:
        img_b64 = _render_region_png_b64(ctx.pdf_path, page_index, crop)
    except Exception as exc:
        yield f"Error rendering page region: {exc}"
        return

    user_text = _load_prompt("figure_explain.user.txt").format(
        figure_id=f"Figure {n}",
        page_human=page_index + 1,
        caption=caption or "(no caption extracted)",
        context=context,
    )
    messages = [
        {"role": "system", "content": _load_prompt("figure_explain.system.txt")},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                },
            ],
        },
    ]

    acc: list[str] = []
    try:
        for token in llm_client.stream_vision_messages(messages):
            acc.append(token)
            yield token
    finally:
        full = "".join(acc).strip()
        if full:
            try:
                out = cache_path(ctx.data_dir, synthetic_id)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(full, encoding="utf-8")
            except Exception as exc:
                print(f"[figure_explain] cache write failed: {exc}", file=sys.stderr)


def stream(ctx, figure_id: str) -> Iterator[str]:
    """Yield tokens of the figure explanation. Hits disk cache when present;
    otherwise calls the vision LLM and writes the cache on completion."""
    if not _ID_SAFE_RE.match(figure_id or ""):
        yield f"Invalid figure id: {figure_id!r}"
        return

    cached = load(ctx.data_dir, figure_id)
    if cached:
        yield cached
        return

    found = _find_figure(ctx.paper, figure_id)
    if found is None:
        yield f"Figure not found: {figure_id}"
        return
    page_index, fig = found
    caption = fig.get("caption")

    context = _collect_context(ctx.rag_index, caption, page_index)

    try:
        img_b64 = _render_page_png_b64(ctx.pdf_path, page_index)
    except Exception as exc:
        yield f"Error rendering page: {exc}"
        return

    user_text = _load_prompt("figure_explain.user.txt").format(
        figure_id=figure_id,
        page_human=page_index + 1,
        caption=caption or "(no caption extracted)",
        context=context,
    )
    messages = [
        {"role": "system", "content": _load_prompt("figure_explain.system.txt")},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                },
            ],
        },
    ]

    acc: list[str] = []
    try:
        for token in llm_client.stream_vision_messages(messages):
            acc.append(token)
            yield token
    finally:
        full = "".join(acc).strip()
        if full:
            try:
                out = cache_path(ctx.data_dir, figure_id)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(full, encoding="utf-8")
            except Exception as exc:
                print(f"[figure_explain] cache write failed: {exc}", file=sys.stderr)
