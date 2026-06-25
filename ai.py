"""Streaming prompt wrappers: define / explain / chat (RAG).

Prompt templates live in ./prompts/*.txt and are reloaded on every call,
so edits take effect without restarting the server.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import llm_client
from rag import RagIndex

import sys
_PROMPTS_DIR = (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent) / "prompts"


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


def grammar_stream(sentence: str, before: str, after: str) -> Iterator[str]:
    """Break a selected sentence into its grammatical parts for an English learner."""
    user = _load("grammar.user.txt").format(
        sentence=sentence.strip(), before=before.strip(), after=after.strip()
    )
    yield from llm_client.stream_messages([
        {"role": "system", "content": _load("grammar.system.txt")},
        {"role": "user", "content": user},
    ])


def paraphrase_stream(sentence: str, before: str, after: str) -> Iterator[str]:
    """Rewrite a hard sentence/paragraph in simpler English, keeping the meaning."""
    user = _load("paraphrase.user.txt").format(
        sentence=sentence.strip(), before=before.strip(), after=after.strip()
    )
    yield from llm_client.stream_messages([
        {"role": "system", "content": _load("paraphrase.system.txt")},
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


def _building_notice(build_status: str | None) -> str:
    """One-line notice shown when whole-paper chat falls back to vector RAG because the
    GraphRAG index isn't ready yet."""
    if build_status == "failed":
        return ("> ⚠️ The knowledge-graph index for this paper failed to build — answering "
                "from vector search. Use **Rebuild index** to retry.\n\n")
    return ("> ⏳ Building this paper's knowledge graph (one-time). Answering from vector "
            "search meanwhile…\n\n")


def _page_context_block(
    index: RagIndex, message: str, page_index: int, page_markdown: str | None
) -> str:
    """Full markdown for a page if short enough, else its top-k relevant chunks."""
    if page_markdown and len(page_markdown) <= 8000:
        return f"[page {page_index + 1}]\n{page_markdown.strip()}"
    hits = index.topk(message, k=3, page_filter=page_index)
    return _format_context(hits) if hits else (page_markdown or "")


def chat_stream(
    index: RagIndex,
    message: str,
    history: list[dict],
    scope: str = "paper",
    page_index: int | None = None,
    page_markdown: str | None = None,
    graphrag_engine: object | None = None,
    build_status: str | None = None,
    use_graph: bool = True,
    prev_markdown: str | None = None,
    next_markdown: str | None = None,
) -> Iterator[str]:
    """RAG chat.

    scope='page' restricts to a single page (full markdown if short, else top-k within
    the page). ``prev_markdown``/``next_markdown``, when given, add the adjacent page's
    content so a section that spills across a page boundary can still be answered fully.
    scope='paper' (whole paper) runs the agentic GraphRAG pipeline when the paper's
    per-paper index is ready (``graphrag_engine`` provided); until then it degrades to
    the original vector top-k path so chat always answers.

    use_graph=False keeps the agentic plan/search/sufficiency-gate/synthesis workflow but
    excludes GraphRAG search, grounding every step on hybrid (vector+keyword) retrieval only.
    """
    if scope == "page" and page_index is not None:
        parts = []
        if prev_markdown is not None:
            parts.append(_page_context_block(index, message, page_index - 1, prev_markdown))
        parts.append(_page_context_block(index, message, page_index, page_markdown))
        if next_markdown is not None:
            parts.append(_page_context_block(index, message, page_index + 1, next_markdown))
        context = "\n\n".join(parts)

        extra = []
        if prev_markdown is not None:
            extra.append(str(page_index))
        if next_markdown is not None:
            extra.append(str(page_index + 2))
        if extra:
            scope_note = (
                f"Primarily answer using page {page_index + 1}; page(s) {', '.join(extra)} "
                "are also included since the relevant section may continue there."
            )
        else:
            scope_note = f"You must answer using only page {page_index + 1}."
    else:
        # Whole-paper: agentic GraphRAG when the index is ready.
        if graphrag_engine is not None:
            import agentic_rag  # lazy: pulls in graphrag only when actually used
            yield from agentic_rag.stream_with_trace(message, graphrag_engine, use_graph=use_graph)
            return
        # Index not ready (building / failed) -> notice + vector fallback.
        yield _building_notice(build_status)
        section = index.find_section_page(message)
        if section is not None:
            hits = index.topk(message, k=5, page_filter=section["page"])
            context = _format_context(hits)
            scope_note = (
                f"The question refers to the '{section['title']}' section, which begins "
                f"on page {section['page'] + 1}. Answer using those excerpts and "
                f"cite [page {section['page'] + 1}]."
            )
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
