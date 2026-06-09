"""Per-page keyword highlighting: LLM extract keywords, cache to disk."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import llm_client

_PROMPTS_DIR = (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent) / "prompts"
_CACHE_NAME = "highlights.json"
_MAX_PAGE_CHARS = 12000


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def cache_path(data_dir: Path) -> Path:
    return data_dir / _CACHE_NAME


def load_cache(data_dir: Path) -> dict[str, dict[str, Any]]:
    p = cache_path(data_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[highlights] cache read failed: {exc}", file=sys.stderr)
        return {}


def save_cache(data_dir: Path, cache: dict[str, dict[str, Any]]) -> None:
    p = cache_path(data_dir)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _parse_keywords(raw: str) -> list[str]:
    """Extract a JSON list of strings from a model response, tolerant of fences."""
    text = raw.strip()
    # Strip ```json fences if present
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Find the outermost JSON array
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        arr = json.loads(text[start : end + 1])
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for x in arr:
        if not isinstance(x, str):
            continue
        s = x.strip()
        if not s or len(s) > 120:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def highlight_page(
    page_idx: int,
    pages: list[dict[str, Any]],
    cache: dict[str, dict[str, Any]],
    data_dir: Path,
) -> dict[str, Any]:
    key = str(page_idx)
    existing = cache.get(key)
    # Resumable: skip a page that already succeeded. A record carrying an
    # `error` field is a failed attempt, so leave it eligible for retry on a
    # re-run. (A genuinely empty keyword list with no error counts as done.)
    if existing and isinstance(existing.get("keywords"), list) and not existing.get("error"):
        return existing

    md = (pages[page_idx].get("markdown") or "").strip()
    if not md:
        rec = {
            "page_index": page_idx,
            "keywords": [],
            "done_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        cache[key] = rec
        save_cache(data_dir, cache)
        return rec

    text = md if len(md) <= _MAX_PAGE_CHARS else md[:_MAX_PAGE_CHARS] + "\n\n[…truncated]"
    system = _load_prompt("highlight.system.txt")
    user = f"[page {page_idx + 1}]\n{text}"

    error: str | None = None
    keywords: list[str] = []
    try:
        raw = llm_client.chat(system, user)
        keywords = _parse_keywords(raw)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        print(f"[highlights] error on page {page_idx}: {exc}", file=sys.stderr)

    rec: dict[str, Any] = {
        "page_index": page_idx,
        "keywords": keywords,
        "done_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if error:
        rec["error"] = error
    cache[key] = rec
    save_cache(data_dir, cache)
    return rec


def highlight_all(
    pages: list[dict[str, Any]],
    cache: dict[str, dict[str, Any]],
    data_dir: Path,
) -> Iterator[dict[str, Any]]:
    for i in range(len(pages)):
        yield highlight_page(i, pages, cache, data_dir)
