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

# Marker written into a page's summary when the LLM call fails. A re-run
# treats summaries starting with this prefix as "not done" and retries them.
_SUMMARY_ERROR_PREFIX = "[summary error:"


def _is_failed_summary(summary: str | None) -> bool:
    """True if a page has no usable summary yet (never summarized, or the last
    attempt errored), so a manual re-run should (re)process it."""
    s = (summary or "").strip()
    return not s or s.startswith(_SUMMARY_ERROR_PREFIX)


def summarize_pages(pages: list[dict[str, Any]], force: bool = False) -> None:
    """Fill 'summary' field on each page in-place.

    By default this is resumable: a page that already has a good summary is
    skipped, so re-running only retries pages that are empty or errored — the
    "retry failed only" behavior. Pass force=True to re-summarize every page
    (e.g. after changing the prompt)."""
    done = skipped = failed = 0
    # Pages that this run will actually summarize (mirrors the skip logic in the
    # loop below). Used so the progress percentage reaches 100% on completion.
    total = sum(
        1 for p in pages
        if p.get("markdown", "").strip()
        and (force or _is_failed_summary(p.get("summary")))
    )
    if total == 0:
        # Nothing to do (all pages already summarized) — emit a single 100% tick
        # so the UI shows the bar complete instead of never moving.
        print("[pipeline] progress 0/0 (100%)")
    processed = 0
    for p in pages:
        md = p.get("markdown", "")
        if not md.strip():
            p["summary"] = ""
            continue
        if not force and not _is_failed_summary(p.get("summary")):
            skipped += 1
            continue
        try:
            p["summary"] = llm_client.chat(_SUMMARY_SYSTEM, f"Page content:\n\n{md}")
            done += 1
        except Exception as exc:
            p["summary"] = f"{_SUMMARY_ERROR_PREFIX} {exc}]"
            failed += 1
            print(f"[pipeline] summary error on page {p['index']}: {exc}", file=sys.stderr)
        processed += 1
        pct = round(100 * processed / total) if total else 100
        # Progress line consumed in-place by the viewer's status box (and the tee
        # in serve._stream_job). Keep the "[pipeline] progress N/M (P%)" shape.
        print(f"[pipeline] progress {processed}/{total} ({pct}%)")
    print(f"[pipeline] summarized {done} page(s), skipped {skipped} already-good, "
          f"{failed} failed (of {len(pages)} total)", file=sys.stderr)
