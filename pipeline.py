"""Orchestrate: extract -> toc -> summarize -> RAG index -> paper.json."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import llm_client


_SUMMARY_SYSTEM = (
    "You are a paper-reading assistant for a non-native English reader. "
    "Summarize one page of an ML/CS paper in EASY ENGLISH — 2 to 4 short "
    "sentences, common words, define jargon inline when you mention it. "
    "Use Markdown. Do NOT add a heading; output just the summary text."
)


def summarize_pages(pages: list[dict[str, Any]]) -> None:
    """Fill 'summary' field on each page in-place."""
    for p in pages:
        md = p.get("markdown", "")
        if not md.strip():
            p["summary"] = ""
            continue
        try:
            p["summary"] = llm_client.chat(_SUMMARY_SYSTEM, f"Page content:\n\n{md}")
        except Exception as exc:
            p["summary"] = f"[summary error: {exc}]"
            print(f"[pipeline] summary error on page {p['index']}: {exc}", file=sys.stderr)
    print(f"[pipeline] summarized {len(pages)} pages", file=sys.stderr)
