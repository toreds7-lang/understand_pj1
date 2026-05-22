"""3-tier TOC builder: fitz bookmarks -> pdfplumber TOC-page regex -> font heuristic."""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import fitz
import pdfplumber


# ---------------------------------------------------------------------------
# Tier 1 — fitz embedded bookmarks
# ---------------------------------------------------------------------------

def _from_bookmarks(doc: fitz.Document) -> list[dict[str, Any]]:
    raw = doc.get_toc(simple=True) or []
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if len(item) < 3:
            continue
        lvl, title, page = item[0], item[1], item[2]
        out.append({
            "level": max(1, min(3, int(lvl))),
            "title": str(title).strip(),
            "page": max(0, int(page) - 1),
            "anchor": f"toc-{i}",
        })
    return out


# ---------------------------------------------------------------------------
# Tier 2 — pdfplumber TOC-page regex
# ---------------------------------------------------------------------------

_TOC_HEADER_RE = re.compile(r"(?im)^\s*(table of contents|contents)\s*$")
_TOC_LINE_RE = re.compile(
    r"^\s*(?P<num>\d+(?:\.\d+)*)?\s*(?P<title>.+?)\s*\.{2,}\s*(?P<page>\d+)\s*$"
)


def _from_toc_page(pdf_path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            scan = pdf.pages[: min(12, len(pdf.pages))]
            for ppage in scan:
                text = ppage.extract_text() or ""
                if not _TOC_HEADER_RE.search(text):
                    continue
                anchor_i = 0
                for ln in text.split("\n"):
                    m = _TOC_LINE_RE.match(ln)
                    if not m:
                        continue
                    num = m.group("num") or ""
                    title = m.group("title").strip()
                    try:
                        page = max(0, int(m.group("page")) - 1)
                    except ValueError:
                        continue
                    level = 1 + num.count(".") if num else 1
                    level = max(1, min(3, level))
                    full = f"{num} {title}".strip() if num else title
                    out.append({
                        "level": level,
                        "title": full,
                        "page": page,
                        "anchor": f"toc-{anchor_i}",
                    })
                    anchor_i += 1
                if out:
                    break
    except Exception as exc:
        print(f"[toc] pdfplumber tier failed: {exc}", file=sys.stderr)
    return out


# ---------------------------------------------------------------------------
# Tier 3 — fitz font-size heuristic
# ---------------------------------------------------------------------------

def _from_font_heuristic(doc: fitz.Document) -> list[dict[str, Any]]:
    sizes: list[float] = []
    spans_per_page: list[list[tuple[float, str]]] = []
    for page in doc:
        page_spans: list[tuple[float, str]] = []
        try:
            data = page.get_text("dict")
        except Exception:
            spans_per_page.append([])
            continue
        for block in data.get("blocks", []):
            for line in block.get("lines", []):
                line_text = ""
                line_size = 0.0
                for span in line.get("spans", []):
                    txt = (span.get("text") or "").strip()
                    if not txt:
                        continue
                    line_text += (" " if line_text else "") + txt
                    line_size = max(line_size, float(span.get("size", 0.0)))
                if line_text and line_size > 0:
                    sizes.append(line_size)
                    page_spans.append((line_size, line_text))
        spans_per_page.append(page_spans)

    if not sizes:
        return []
    sizes.sort()
    median = sizes[len(sizes) // 2]
    threshold = median * 1.4

    candidates: list[tuple[int, float, str]] = []
    for i, page_spans in enumerate(spans_per_page):
        for sz, txt in page_spans:
            if sz >= threshold and len(txt) <= 120:
                candidates.append((i, sz, txt))

    if not candidates:
        return []

    big_sizes = sorted({c[1] for c in candidates}, reverse=True)
    def lvl(sz: float) -> int:
        if sz >= big_sizes[0] - 0.5:
            return 1
        if len(big_sizes) > 1 and sz >= big_sizes[1] - 0.5:
            return 2
        return 3

    out: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for i, (pg, sz, txt) in enumerate(candidates):
        key = (pg, txt.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "level": lvl(sz),
            "title": txt,
            "page": pg,
            "anchor": f"toc-{i}",
        })
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_toc(pdf_path: Path) -> list[dict[str, Any]]:
    doc = fitz.open(str(pdf_path))
    try:
        toc = _from_bookmarks(doc)
        if toc:
            print(f"[toc] tier=bookmarks entries={len(toc)}", file=sys.stderr)
            return toc

        toc = _from_toc_page(pdf_path)
        if toc:
            print(f"[toc] tier=toc-page-regex entries={len(toc)}", file=sys.stderr)
            return toc

        toc = _from_font_heuristic(doc)
        print(f"[toc] tier=font-heuristic entries={len(toc)}", file=sys.stderr)
        return toc
    finally:
        doc.close()
