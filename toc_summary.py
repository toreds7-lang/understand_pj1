"""Hierarchical TOC summaries: build tree, summarize bottom-up, cache to disk."""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import llm_client

_PROMPTS_DIR = (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent) / "prompts"
_CACHE_NAME = "toc_summaries.json"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


@dataclass
class Node:
    anchor: str
    title: str
    level: int
    page_start: int
    page_end: int
    children: list["Node"] = field(default_factory=list)


def build_tree(toc: list[dict[str, Any]], num_pages: int) -> list[Node]:
    """Build a forest from the flat TOC list. Uses a stack and the `level` field."""
    if not toc:
        return []

    nodes: list[Node] = [
        Node(
            anchor=str(entry.get("anchor", f"toc-{i}")),
            title=str(entry.get("title", "")).strip() or f"Section {i}",
            level=int(entry.get("level", 1)),
            page_start=int(entry.get("page", 0)),
            page_end=num_pages - 1,
        )
        for i, entry in enumerate(toc)
    ]

    # page_end = (next entry's page) - 1, else last page
    for i, n in enumerate(nodes):
        nxt_page = num_pages
        for j in range(i + 1, len(nodes)):
            if nodes[j].page_start >= n.page_start:
                nxt_page = nodes[j].page_start
                break
        n.page_end = max(n.page_start, nxt_page - 1)
        if n.page_end > num_pages - 1:
            n.page_end = num_pages - 1

    roots: list[Node] = []
    stack: list[Node] = []
    for n in nodes:
        while stack and stack[-1].level >= n.level:
            stack.pop()
        if stack:
            stack[-1].children.append(n)
        else:
            roots.append(n)
        stack.append(n)
    return roots


def _flatten(roots: list[Node]) -> list[Node]:
    out: list[Node] = []
    def walk(n: Node) -> None:
        out.append(n)
        for c in n.children:
            walk(c)
    for r in roots:
        walk(r)
    return out


def find_node_by_anchor(roots: list[Node], anchor: str) -> Node | None:
    for n in _flatten(roots):
        if n.anchor == anchor:
            return n
    return None


def find_node_for_page(roots: list[Node], page_idx: int) -> Node | None:
    """Return deepest node whose [page_start, page_end] contains page_idx."""
    best: Node | None = None
    for n in _flatten(roots):
        if n.page_start <= page_idx <= n.page_end:
            if best is None or n.level > best.level:
                best = n
    return best


def breadcrumb(roots: list[Node], anchor: str) -> list[dict[str, Any]]:
    """Return ancestor chain (root first) for the node with this anchor."""
    trail: list[Node] = []
    def walk(node: Node, path: list[Node]) -> bool:
        path.append(node)
        if node.anchor == anchor:
            trail.extend(path)
            return True
        for c in node.children:
            if walk(c, path):
                return True
        path.pop()
        return False
    for r in roots:
        if walk(r, []):
            break
    return [{"anchor": n.anchor, "title": n.title, "level": n.level} for n in trail]


# ─── Cache I/O ────────────────────────────────────────────────────────────────

def cache_path(data_dir: Path) -> Path:
    return data_dir / _CACHE_NAME


def load_cache(data_dir: Path) -> dict[str, dict[str, Any]]:
    p = cache_path(data_dir)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[toc_summary] cache read failed: {exc}", file=sys.stderr)
        return {}


def save_cache(data_dir: Path, cache: dict[str, dict[str, Any]]) -> None:
    p = cache_path(data_dir)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


# ─── Summarization ────────────────────────────────────────────────────────────

# Cap how much raw markdown we feed the LLM per leaf, to avoid huge prompts.
_MAX_LEAF_CHARS = 16000


def _leaf_input(node: Node, pages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for i in range(node.page_start, node.page_end + 1):
        if 0 <= i < len(pages):
            md = (pages[i].get("markdown") or "").strip()
            if md:
                parts.append(f"[page {i + 1}]\n{md}")
    text = "\n\n".join(parts)
    if len(text) > _MAX_LEAF_CHARS:
        text = text[:_MAX_LEAF_CHARS] + "\n\n[…truncated]"
    return text


def _parent_input(node: Node, pages: list[dict[str, Any]],
                  cache: dict[str, dict[str, Any]]) -> str:
    # Intro text: pages between node.page_start and first child's page_start - 1
    intro_end = node.children[0].page_start - 1 if node.children else node.page_end
    intro_end = min(intro_end, node.page_end)
    intro_parts: list[str] = []
    for i in range(node.page_start, intro_end + 1):
        if 0 <= i < len(pages):
            md = (pages[i].get("markdown") or "").strip()
            if md:
                intro_parts.append(f"[page {i + 1}]\n{md}")
    intro = "\n\n".join(intro_parts)
    if len(intro) > _MAX_LEAF_CHARS // 2:
        intro = intro[: _MAX_LEAF_CHARS // 2] + "\n\n[…truncated]"

    child_bullets: list[str] = []
    for c in node.children:
        entry = cache.get(c.anchor)
        s = (entry or {}).get("summary", "").strip() if entry else ""
        if not s:
            s = "(no summary)"
        child_bullets.append(f"- **{c.title}** (pp. {c.page_start + 1}–{c.page_end + 1}): {s}")

    out = f"Section: {node.title}\n\n"
    if intro:
        out += f"Intro text:\n{intro}\n\n"
    out += "Child subsections:\n" + "\n".join(child_bullets)
    return out


_SUMMARY_ERROR_PREFIX = "[summary error:"


def _record(node: Node, summary: str, error: str | None = None) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "anchor": node.anchor,
        "title": node.title,
        "level": node.level,
        "page_start": node.page_start,
        "page_end": node.page_end,
        "summary": summary,
        "done_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if error:
        rec["error"] = error
    return rec


def _is_failed(entry: dict[str, Any] | None) -> bool:
    """True if a node has no usable summary yet (never summarized, or the last
    attempt errored), so a manual re-run should (re)process it."""
    if not entry:
        return True
    if entry.get("error"):
        return True
    s = (entry.get("summary") or "").strip()
    return not s or s.startswith(_SUMMARY_ERROR_PREFIX)


def summarize_node(
    node: Node,
    pages: list[dict[str, Any]],
    cache: dict[str, dict[str, Any]],
    data_dir: Path,
    on_done: Callable[[dict[str, Any]], None] | None = None,
    dirty: set[str] | None = None,
) -> Iterator[dict[str, Any]]:
    """Post-order: recursively summarize children, then this node. Yields each
    completed record as `{anchor, title, level, page_start, page_end, summary,
    done_at}`. Cache is written after each completion.

    Resumable by default: a node that already has a good summary is skipped, so
    re-running only retries nodes that are missing or errored. A parent is also
    re-summarized when any of its children were (re)computed in this run (their
    anchors are collected in `dirty`), so a parent that previously folded in a
    failed child's "(no summary)" bullet is refreshed once the child recovers."""
    if dirty is None:
        dirty = set()

    # Children first
    for c in node.children:
        yield from summarize_node(c, pages, cache, data_dir, on_done, dirty)

    child_changed = any(c.anchor in dirty for c in node.children)
    if not _is_failed(cache.get(node.anchor)) and not child_changed:
        return

    if node.children:
        system = _load_prompt("toc_parent.system.txt")
        user = _parent_input(node, pages, cache)
    else:
        system = _load_prompt("toc_leaf.system.txt")
        user = _leaf_input(node, pages)
        if not user.strip():
            user = f"Section: {node.title}\n\n[no text available for pages " \
                   f"{node.page_start + 1}–{node.page_end + 1}]"

    error: str | None = None
    try:
        summary = llm_client.chat(system, user)
    except Exception as exc:
        summary = f"{_SUMMARY_ERROR_PREFIX} {exc}]"
        error = f"{type(exc).__name__}: {exc}"
        print(f"[toc_summary] error on {node.anchor} ({node.title}): {exc}",
              file=sys.stderr)

    rec = _record(node, summary, error=error)
    cache[node.anchor] = rec
    save_cache(data_dir, cache)
    # Only a successful (re)compute should cascade to ancestors; a failed node
    # stays in the failed set and is retried (with its parent) on the next run.
    if error is None:
        dirty.add(node.anchor)
    if on_done is not None:
        on_done(rec)
    yield rec


def summarize_all(
    roots: list[Node],
    pages: list[dict[str, Any]],
    cache: dict[str, dict[str, Any]],
    data_dir: Path,
) -> Iterator[dict[str, Any]]:
    dirty: set[str] = set()
    for r in roots:
        yield from summarize_node(r, pages, cache, data_dir, dirty=dirty)
