"""Per-page chunking + embedding index for RAG chat."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

import llm_client


# rough token approx: 4 chars/token
_CHUNK_CHARS = 2000
_OVERLAP_CHARS = 200

# A markdown heading line: one or more '#', then the (possibly bold/numbered) title.
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
# Leading section numbering to strip: "1", "7.2", "A.", "IV)" etc.
_NUMBERING_RE = re.compile(r"^[\dIVXLivxl]+(?:\.\d+)*[.)]?\s+")


def _clean_heading(raw: str) -> str:
    """Normalize a raw heading to its bare section name.
    '## **1 Introduction**' -> 'Introduction', '**Abstract**' -> 'Abstract'."""
    s = raw.replace("*", "").replace("`", "").strip()
    s = s.strip("_").strip()  # drop surrounding italic markers
    s = _NUMBERING_RE.sub("", s).strip()
    return s


def _chunk_text(text: str, page_index: int) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    text = text.strip()
    if not text:
        return chunks
    i = 0
    while i < len(text):
        seg = text[i : i + _CHUNK_CHARS]
        chunks.append({"page": page_index, "text": seg})
        if i + _CHUNK_CHARS >= len(text):
            break
        i += _CHUNK_CHARS - _OVERLAP_CHARS
    return chunks


def build_index(pages: list[dict[str, Any]], data_dir: Path) -> None:
    """Embed all chunks and save vectors + metadata to data_dir."""
    all_chunks: list[dict[str, Any]] = []
    for p in pages:
        all_chunks.extend(_chunk_text(p["markdown"], p["index"]))
    if not all_chunks:
        print("[rag] no chunks to index", file=sys.stderr)
        return

    print(f"[rag] embedding {len(all_chunks)} chunks", file=sys.stderr)
    vecs_parts: list[np.ndarray] = []
    BATCH = 64
    for i in range(0, len(all_chunks), BATCH):
        batch = [c["text"] for c in all_chunks[i : i + BATCH]]
        vecs_parts.append(llm_client.embed(batch))
    vecs = np.vstack(vecs_parts).astype(np.float32)

    np.savez_compressed(data_dir / "rag.npz", vecs=vecs)
    (data_dir / "rag.json").write_text(
        json.dumps(all_chunks, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[rag] wrote rag.npz ({vecs.shape}) + rag.json", file=sys.stderr)


# ---------------------------------------------------------------------------
# Query side (used by ai.chat_stream)
# ---------------------------------------------------------------------------

class RagIndex:
    """In-memory RAG index loaded once per process."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.vecs: np.ndarray | None = None
        self.chunks: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        vpath = self.data_dir / "rag.npz"
        cpath = self.data_dir / "rag.json"
        if not (vpath.exists() and cpath.exists()):
            print(f"[rag] no index at {self.data_dir}", file=sys.stderr)
            return
        self.vecs = np.load(vpath)["vecs"]
        self.chunks = json.loads(cpath.read_text(encoding="utf-8"))

    def sections(self) -> list[dict[str, Any]]:
        """Extract every markdown heading found in the indexed chunks.
        Returns [{"page": int, "title": str}] in document order, deduped by title."""
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for chunk in self.chunks:
            for m in _HEADING_RE.finditer(chunk["text"]):
                title = _clean_heading(m.group(1))
                key = title.lower()
                if len(title) < 3 or key in seen:
                    continue
                seen.add(key)
                out.append({"page": chunk["page"], "title": title})
        return out

    def find_section_page(self, query: str) -> dict[str, Any] | None:
        """If the query mentions one of the paper's actual section headings, return
        {"page": int, "title": str} for the most specific (longest) match, else None.
        Works for any heading present in the document, not a fixed keyword list."""
        q = query.lower()
        best: dict[str, Any] | None = None
        for sec in self.sections():
            title = sec["title"].lower()
            if re.search(rf"\b{re.escape(title)}\b", q):
                if best is None or len(title) > len(best["title"]):
                    best = sec
        return best

    def topk(self, query: str, k: int = 5, page_filter: int | None = None) -> list[dict[str, Any]]:
        if self.vecs is None or not self.chunks:
            return []
        q = llm_client.embed([query])  # (1, D), already normalized
        sims = (self.vecs @ q[0]).astype(np.float32)
        if page_filter is not None:
            mask = np.array([c["page"] == page_filter for c in self.chunks])
            sims = np.where(mask, sims, -1.0)
        order = np.argsort(-sims)[:k]
        return [
            {**self.chunks[i], "score": float(sims[i])}
            for i in order
            if sims[i] > -1.0
        ]
