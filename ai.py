"""Streaming prompt wrappers: define / explain / chat (RAG).

Prompt templates live in ./prompts/*.txt and are reloaded on every call,
so edits take effect without restarting the server.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import llm_client
from rag import RagIndex

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def define_stream(word: str, before: str, after: str) -> Iterator[str]:
    user = _load("define.user.txt").format(
        word=word.strip(), before=before.strip(), after=after.strip()
    )
    yield from llm_client.stream_messages([
        {"role": "system", "content": _load("define.system.txt")},
        {"role": "user", "content": user},
    ])


def explain_stream(sentence: str, before: str, after: str) -> Iterator[str]:
    user = _load("explain.user.txt").format(
        sentence=sentence.strip(), before=before.strip(), after=after.strip()
    )
    yield from llm_client.stream_messages([
        {"role": "system", "content": _load("explain.system.txt")},
        {"role": "user", "content": user},
    ])


def _format_context(snippets: list[dict]) -> str:
    parts = []
    for s in snippets:
        parts.append(f"[page {s['page'] + 1}]\n{s['text'].strip()}")
    return "\n\n---\n\n".join(parts)


def _format_history(history: list[dict], n_pairs: int = 3) -> str:
    """Take the last n_pairs user+assistant exchanges, formatted as text."""
    pairs: list[tuple[str, str]] = []
    pending_user: str | None = None
    for turn in history:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            pending_user = content
        elif role == "assistant" and pending_user is not None:
            pairs.append((pending_user, content))
            pending_user = None
    pairs = pairs[-n_pairs:]
    if not pairs:
        return "(no prior turns)"
    lines = []
    for u, a in pairs:
        lines.append(f"User: {u}\nAssistant: {a}")
    return "\n\n".join(lines)


def chat_stream(
    index: RagIndex,
    message: str,
    history: list[dict],
    scope: str = "paper",
    page_index: int | None = None,
    page_markdown: str | None = None,
) -> Iterator[str]:
    """RAG chat. scope='paper' searches all chunks; scope='page' restricts to
    a single page (using its full markdown if short, else top-k within page)."""
    if scope == "page" and page_index is not None:
        if page_markdown and len(page_markdown) <= 8000:
            context = f"[page {page_index + 1}]\n{page_markdown.strip()}"
        else:
            hits = index.topk(message, k=3, page_filter=page_index)
            context = _format_context(hits) if hits else (page_markdown or "")
        scope_note = f"You must answer using only page {page_index + 1}."
    else:
        hits = index.topk(message, k=5)
        context = _format_context(hits)
        scope_note = "Use the most relevant excerpts; cite the pages you used."

    history_text = _format_history(history, n_pairs=3)
    user_msg = _load("chat.user.txt").format(
        scope_note=scope_note,
        context=context,
        history=history_text,
        question=message.strip(),
    )

    messages: list[dict] = [
        {"role": "system", "content": _load("chat.system.txt")},
        {"role": "user", "content": user_msg},
    ]

    yield from llm_client.stream_messages(messages)
