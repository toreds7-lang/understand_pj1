"""Library-based PDF -> per-page markdown + figures + tables.

Strategy:
  1. pymupdf4llm gives us page-scoped markdown with headings/tables/math.
  2. pdfplumber refines tables when its row/col count beats pymupdf4llm.
  3. fitz (PyMuPDF) extracts embedded raster figures and patches markdown.
"""
from __future__ import annotations

import base64
import re
import sys
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import pdfplumber
import pymupdf4llm

import llm_client


# ---------------------------------------------------------------------------
# pymupdf4llm primary pass
# ---------------------------------------------------------------------------

def _markdown_per_page(pdf_path: Path, num_pages: int) -> list[str]:
    """Run pymupdf4llm once with page_chunks=True; return one markdown per page."""
    chunks = pymupdf4llm.to_markdown(
        str(pdf_path),
        page_chunks=True,
        write_images=False,
        ignore_images=True,
        ignore_graphics=True,
    )
    by_index = [""] * num_pages
    for ch in chunks:
        # chunk shape: {"metadata": {"page_number": N (1-based), ...}, "text": "..."}
        meta = ch.get("metadata", {}) if isinstance(ch, dict) else {}
        page = meta.get("page_number", meta.get("page"))
        text = ch.get("text", "") if isinstance(ch, dict) else ""
        if page is None:
            continue
        idx = int(page) - 1 if int(page) >= 1 else int(page)
        if 0 <= idx < num_pages:
            by_index[idx] = text
    return by_index


# ---------------------------------------------------------------------------
# vision-LLM markdown pass
# ---------------------------------------------------------------------------

_PROMPTS_DIR = (
    Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
) / "prompts"

_VISION_USER = "Transcribe this page to GitHub-Flavored Markdown."


def _vision_system() -> str:
    return (_PROMPTS_DIR / "extract_vision.system.txt").read_text(encoding="utf-8").strip()


def _render_page_png_b64(page, zoom: float = 2.0) -> str:
    """Render a full page to a PNG and return it base64-encoded."""
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return base64.b64encode(pix.tobytes("png")).decode("ascii")


def _vision_page_markdown(page, system: str, user: str) -> tuple[str | None, str | None]:
    """Render one page and transcribe it with the vision LLM. Returns
    (markdown, error): markdown is None on failure or empty output, and error is
    a message string when the attempt failed (None on success)."""
    try:
        b64 = _render_page_png_b64(page)
        md = (llm_client.vision_chat(system, user, b64) or "").strip()
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not md:
        return None, "vision returned empty output"
    return md, None


def _markdown_per_page_vision(
    pdf_path: Path, num_pages: int
) -> tuple[list[str | None], list[str | None]]:
    """Transcribe each page to markdown with the vision LLM. Returns
    (markdowns, errors): a markdown entry is None when extraction failed or
    returned nothing so the caller can fall back to the text pass for just those
    pages, and the matching errors entry carries why it failed."""
    system = _vision_system()
    out: list[str | None] = [None] * num_pages
    errors: list[str | None] = [None] * num_pages
    doc = fitz.open(str(pdf_path))
    try:
        for i, page in enumerate(doc):
            if i >= num_pages:
                break
            md, err = _vision_page_markdown(page, system, _VISION_USER)
            out[i] = md
            errors[i] = err
            if md is not None:
                print(f"[extract] page {i}: vision markdown ({len(md)} chars)", file=sys.stderr)
            else:
                print(f"[extract] page {i}: vision extraction failed: {err}", file=sys.stderr)
    finally:
        doc.close()
    return out, errors


# ---------------------------------------------------------------------------
# pdfplumber table refinement
# ---------------------------------------------------------------------------

_GFM_TABLE_RE = re.compile(
    r"((?:^\|[^\n]*\|\s*\n)(?:^\|[\s\-:|]+\|\s*\n)(?:^\|[^\n]*\|\s*\n)+)",
    re.MULTILINE,
)


def _gfm_from_rows(rows: list[list[str | None]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    cleaned: list[list[str]] = []
    for r in rows:
        cells = [(c or "").replace("\n", " ").replace("|", "\\|").strip() for c in r]
        while len(cells) < width:
            cells.append("")
        cleaned.append(cells)
    header = cleaned[0]
    body = cleaned[1:] if len(cleaned) > 1 else []
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _count_rows_cols(gfm: str) -> tuple[int, int]:
    lines = [ln for ln in gfm.strip().split("\n") if ln.strip().startswith("|")]
    if not lines:
        return 0, 0
    cols = lines[0].count("|") - 1
    rows = max(0, len(lines) - 1)  # minus the separator
    return rows, cols


def _refine_tables_per_page(
    pdf_path: Path,
    page_markdowns: list[str],
) -> list[list[dict[str, Any]]]:
    """For each page, extract tables via pdfplumber and replace pymupdf4llm GFM
    blocks whose dimensions differ noticeably. Also return structured tables
    for paper.json.
    """
    page_tables: list[list[dict[str, Any]]] = [[] for _ in page_markdowns]
    with pdfplumber.open(str(pdf_path)) as pdf:
        for i, ppage in enumerate(pdf.pages):
            if i >= len(page_markdowns):
                break
            try:
                tables = ppage.find_tables() or []
            except Exception as exc:
                print(f"[extract] page {i}: pdfplumber.find_tables failed: {exc}", file=sys.stderr)
                continue

            md = page_markdowns[i]
            existing = list(_GFM_TABLE_RE.finditer(md))
            replaced = 0
            for t_idx, tbl in enumerate(tables):
                try:
                    rows = tbl.extract()
                except Exception:
                    continue
                if not rows or not any(any(c for c in r) for r in rows):
                    continue
                new_gfm = _gfm_from_rows(rows)
                new_r, new_c = _count_rows_cols(new_gfm)
                page_tables[i].append({
                    "bbox": list(tbl.bbox) if hasattr(tbl, "bbox") else None,
                    "rows": rows,
                })
                if t_idx < len(existing):
                    old_gfm = existing[t_idx].group(1)
                    old_r, old_c = _count_rows_cols(old_gfm)
                    if abs(new_c - old_c) > 1 or new_r > old_r + 1:
                        md = md.replace(old_gfm, new_gfm + "\n", 1)
                        replaced += 1
                else:
                    # pymupdf4llm missed a table — append at end of page.
                    md = md.rstrip() + "\n\n" + new_gfm + "\n"
                    replaced += 1
            if replaced:
                page_markdowns[i] = md
                print(f"[extract] page {i}: refined/added {replaced} table(s) via pdfplumber",
                      file=sys.stderr)
    return page_tables


# ---------------------------------------------------------------------------
# fitz figure extraction
# ---------------------------------------------------------------------------

_CAPTION_HEAD_RE = re.compile(r"^\s*(?:Figure|Fig\.?)\s*(\d+)", re.IGNORECASE)


def _find_caption_blocks(page) -> list[tuple[str, str, tuple[float, float, float, float]]]:
    """Return [(figure_number, caption_text, bbox), ...] for all 'Figure N ...' blocks on the page."""
    try:
        blocks = page.get_text("blocks") or []
    except Exception:
        return []
    out = []
    for b in blocks:
        if len(b) < 5:
            continue
        x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
        if not isinstance(text, str) or not text.strip():
            continue
        first_line = text.strip().splitlines()[0]
        m = _CAPTION_HEAD_RE.match(first_line)
        if not m:
            continue
        out.append((m.group(1), text.strip().replace("\n", " "),
                    (float(x0), float(y0), float(x1), float(y1))))
    return out


def _assign_to_caption(img_bbox, captions) -> int | None:
    """Pick the caption block closest below the image (with horizontal overlap)."""
    if not captions or img_bbox is None:
        return None
    ix0, iy0, ix1, iy1 = img_bbox
    best_idx = None
    best_gap = 1e9
    for idx, (_num, _text, (cx0, cy0, cx1, cy1)) in enumerate(captions):
        if cx1 < ix0 - 20 or cx0 > ix1 + 20:
            continue
        gap = cy0 - iy1
        if gap < -30:
            continue
        # Reject if another caption sits strictly between the image and this one.
        blocked = False
        for j_idx, (_n2, _t2, (_, oy0, _, _oy1)) in enumerate(captions):
            if j_idx == idx:
                continue
            if iy1 < oy0 < cy0 - 5:
                blocked = True
                break
        if blocked:
            continue
        if gap < best_gap:
            best_gap = gap
            best_idx = idx
    return best_idx


def _union_bbox(boxes):
    xs0 = min(b[0] for b in boxes)
    ys0 = min(b[1] for b in boxes)
    xs1 = max(b[2] for b in boxes)
    ys1 = max(b[3] for b in boxes)
    return (xs0, ys0, xs1, ys1)


def _render_region_png(page, bbox, out_path: Path, zoom: float = 2.0) -> None:
    """Render the given page region (in PDF points) to PNG."""
    clip = fitz.Rect(*bbox)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    pix.save(str(out_path))


def _extract_figures(
    pdf_path: Path,
    figures_dir: Path,
    page_markdowns: list[str],
) -> list[list[dict[str, Any]]]:
    """Group embedded images on each page by their nearest 'Figure N: ...' caption,
    render one composite PNG per group, and emit one figure record per group."""
    figures_dir.mkdir(parents=True, exist_ok=True)
    page_figures: list[list[dict[str, Any]]] = [[] for _ in page_markdowns]
    doc = fitz.open(str(pdf_path))
    try:
        for i, page in enumerate(doc):
            if i >= len(page_markdowns):
                break
            pw, ph = float(page.rect.width), float(page.rect.height)
            captions = _find_caption_blocks(page)

            # Collect (bbox, caption_idx) for every embedded image on this page.
            raw: list[tuple[tuple[float, float, float, float], int | None]] = []
            for k, info in enumerate(page.get_images(full=True)):
                try:
                    r = page.get_image_bbox(info)
                    bbox = (float(r.x0), float(r.y0), float(r.x1), float(r.y1))
                except Exception as bexc:
                    print(f"[extract] page {i} fig {k}: bbox failed: {bexc}", file=sys.stderr)
                    continue
                if bbox[2] - bbox[0] < 1 or bbox[3] - bbox[1] < 1:
                    continue
                cap_idx = _assign_to_caption(bbox, captions)
                raw.append((bbox, cap_idx))

            # Group: images sharing the same caption merge into one figure.
            groups: dict[object, list[tuple[float, float, float, float]]] = {}
            order: list[object] = []
            for k, (bbox, cap_idx) in enumerate(raw):
                key = ("cap", cap_idx) if cap_idx is not None else ("solo", k)
                if key not in groups:
                    groups[key] = []
                    order.append(key)
                groups[key].append(bbox)

            for g_idx, key in enumerate(order):
                boxes = groups[key]
                # If grouped by caption, also include the caption's own bbox so it appears in the figure.
                cap_text = None
                fig_num = None
                if key[0] == "cap":
                    cap_idx = key[1]
                    fig_num, cap_text, cap_bbox = captions[cap_idx]
                    boxes = boxes + [cap_bbox]
                ubox = _union_bbox(boxes)
                # Pad slightly so edges aren't clipped.
                pad = 4.0
                rbox = (
                    max(0.0, ubox[0] - pad),
                    max(0.0, ubox[1] - pad),
                    min(pw, ubox[2] + pad),
                    min(ph, ubox[3] + pad),
                )

                fid = f"figure_{i:03d}_{g_idx}"
                fname = f"{fid}.png"
                fpath = figures_dir / fname
                try:
                    _render_region_png(page, rbox, fpath)
                except Exception as exc:
                    print(f"[extract] page {i} group {g_idx}: render failed: {exc}", file=sys.stderr)
                    continue
                rel = f"figures/{fname}"

                page_figures[i].append({
                    "id": fid,
                    "path": rel,
                    "bbox": [rbox[0], rbox[1], rbox[2], rbox[3]],
                    "page_width": pw,
                    "page_height": ph,
                    "caption": cap_text,
                    "figure_number": fig_num,
                })
                label = f"Figure {fig_num}" if fig_num else f"figure {i}.{g_idx}"
                page_markdowns[i] = (
                    page_markdowns[i].rstrip()
                    + f"\n\n![{label}](/{rel})\n"
                )
    finally:
        doc.close()
    return page_figures


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_pdf(pdf_path: Path, data_dir: Path, mode: str = "text",
                ocr_only: bool = False) -> dict[str, Any]:
    """Extract structured per-page content. Returns a partial paper.json dict
    (without summaries / toc — those are filled by the pipeline).

    mode="text"   -> page markdown via pymupdf4llm (default).
    mode="vision" -> page markdown via the vision LLM, falling back to pymupdf4llm
                     for any page the vision model fails on.
    ocr_only=True -> stop after the page-markdown pass: skip the pdfplumber table
                     refinement and fitz figure extraction (figures/tables stay
                     empty). Pairs with mode="vision" for a pure vision-OCR run.
    """
    doc = fitz.open(str(pdf_path))
    num_pages = doc.page_count
    doc.close()

    # Per-page provenance: "vision" (transcribed by the vision LLM), "text"
    # (pure pymupdf4llm run), or "text-fallback" (vision was tried but failed,
    # so we fell back to text). reocr_failed_pages() retries the last kind.
    extract_methods: list[str]
    extract_errors: list[str | None]
    if mode == "vision":
        print(f"[extract] {num_pages} pages — running vision LLM", file=sys.stderr)
        vision_md, vision_err = _markdown_per_page_vision(pdf_path, num_pages)
        extract_methods = ["vision"] * num_pages
        extract_errors = [None] * num_pages
        missing = [i for i, md in enumerate(vision_md) if md is None]
        if missing:
            print(f"[extract] vision failed on {len(missing)} page(s); "
                  f"falling back to pymupdf4llm for those", file=sys.stderr)
            text_md = _markdown_per_page(pdf_path, num_pages)
            for i in missing:
                vision_md[i] = text_md[i]
                extract_methods[i] = "text-fallback"
                extract_errors[i] = vision_err[i]
        page_markdowns = [md or "" for md in vision_md]
    else:
        print(f"[extract] {num_pages} pages — running pymupdf4llm", file=sys.stderr)
        page_markdowns = _markdown_per_page(pdf_path, num_pages)
        extract_methods = ["text"] * num_pages
        extract_errors = [None] * num_pages

    if ocr_only:
        print(f"[extract] ocr-only — skipping table refinement and figure extraction",
              file=sys.stderr)
        page_tables: list[list[dict[str, Any]]] = [[] for _ in page_markdowns]
        page_figures: list[list[dict[str, Any]]] = [[] for _ in page_markdowns]
    else:
        print(f"[extract] refining tables with pdfplumber", file=sys.stderr)
        page_tables = _refine_tables_per_page(pdf_path, page_markdowns)

        print(f"[extract] extracting figures with fitz", file=sys.stderr)
        figures_dir = data_dir / "figures"
        page_figures = _extract_figures(pdf_path, figures_dir, page_markdowns)

    pages = []
    for i in range(num_pages):
        page_rec: dict[str, Any] = {
            "index": i,
            "markdown": page_markdowns[i],
            "summary": "",
            "figures": page_figures[i],
            "tables": page_tables[i],
            "extract_method": extract_methods[i],
        }
        if extract_errors[i]:
            page_rec["extract_error"] = extract_errors[i]
        pages.append(page_rec)
    return {"num_pages": num_pages, "pages": pages}


def reocr_failed_pages(pdf_path: Path, pages: list[dict[str, Any]]) -> int:
    """Re-run the vision LLM only on pages that previously fell back to text
    (extract_method == 'text-fallback'), patching their markdown and status in
    place. Returns how many pages were successfully re-OCR'd.

    Pages already transcribed by vision and pages from a pure text-mode run
    (extract_method == 'text') are left untouched — this targets failures only,
    so it is safe to call repeatedly until no fallback pages remain."""
    targets = [p for p in pages if p.get("extract_method") == "text-fallback"]
    if not targets:
        print("[extract] re-OCR: no failed (text-fallback) pages to retry", file=sys.stderr)
        return 0

    print(f"[extract] re-OCR: retrying {len(targets)} failed page(s) with vision LLM",
          file=sys.stderr)
    system = _vision_system()
    fixed = 0
    doc = fitz.open(str(pdf_path))
    try:
        page_count = doc.page_count
        for p in targets:
            i = int(p.get("index", -1))
            if not (0 <= i < page_count):
                print(f"[extract] re-OCR: page index {i} out of range — skipping",
                      file=sys.stderr)
                continue
            md, err = _vision_page_markdown(doc[i], system, _VISION_USER)
            if md is None:
                p["extract_error"] = err
                print(f"[extract] re-OCR page {i}: still failing: {err}", file=sys.stderr)
                continue
            p["markdown"] = md
            p["extract_method"] = "vision"
            p.pop("extract_error", None)
            fixed += 1
            print(f"[extract] re-OCR page {i}: vision markdown ({len(md)} chars)",
                  file=sys.stderr)
    finally:
        doc.close()
    print(f"[extract] re-OCR: fixed {fixed}/{len(targets)} page(s)", file=sys.stderr)
    return fixed
