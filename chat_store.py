"""Append-only persistence for saved AI chat responses, scoped per paper.

Mirrors the data/<paper_id>/... convention used by wiki.py and graph.py.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from config import DATA_DIR


def _chat_dir(paper_id: str) -> Path:
    """Return the chats directory for a specific paper."""
    return DATA_DIR / paper_id / "chats"


def ensure_chat_dir(paper_id: str) -> None:
    """Create chats directory for a paper if absent."""
    _chat_dir(paper_id).mkdir(parents=True, exist_ok=True)


def log_path(paper_id: str) -> Path:
    """Return absolute path to chats/log.jsonl for a specific paper."""
    return _chat_dir(paper_id) / "log.jsonl"


def append_entry(
    paper_id: str,
    *,
    query: str,
    answer: str,
    source: str,
    scope: str | None = None,
    page_index: int | None = None,
) -> dict[str, Any]:
    """Append one saved Q&A entry to chats/log.jsonl and return it."""
    ensure_chat_dir(paper_id)
    entry = {
        "id": uuid.uuid4().hex,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": source,
        "scope": scope,
        "page_index": page_index,
        "query": query,
        "answer": answer,
    }
    with open(log_path(paper_id), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def list_entries(paper_id: str) -> list[dict[str, Any]]:
    """Return saved entries for a paper, newest first."""
    p = log_path(paper_id)
    if not p.exists():
        return []
    entries = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    entries.reverse()
    return entries
