"""Wiki page creation, update, index maintenance, log appending, link resolution."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterator

import llm_client
from config import DATA_DIR
from rag import RagIndex

import sys
_PROMPTS_DIR = (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent) / "prompts"


def _wiki_dir(paper_id: str) -> Path:
    """Return the wiki directory for a specific paper."""
    return DATA_DIR / paper_id / "wiki"


def ensure_wiki_dir(paper_id: str) -> None:
    """Create wiki directory for a paper if absent."""
    _wiki_dir(paper_id).mkdir(parents=True, exist_ok=True)


def page_path(paper_id: str, concept_id: str) -> Path:
    """Return absolute path to wiki/<concept_id>.md for a specific paper."""
    return _wiki_dir(paper_id) / f"{concept_id}.md"


def list_pages(paper_id: str) -> list[dict[str, Any]]:
    """Scan wiki/*.md (excluding index.md and log.md), return metadata list.
    Each entry: {"name": "transformer", "path": "transformer.md",
                 "type": str|None, "papers": [str], "updated_at": str}
    Metadata is extracted from the first 5 lines of each file (fast scan).
    """
    ensure_wiki_dir(paper_id)
    wiki_dir = _wiki_dir(paper_id)
    pages = []
    for md_file in sorted(wiki_dir.glob("*.md")):
        if md_file.name in ("index.md", "log.md"):
            continue
        concept_id = md_file.stem
        try:
            content = md_file.read_text(encoding="utf-8")
            lines = content.split("\n")[:6]
            header_line = next((l for l in lines if l.startswith("_Type:")), "")

            node_type = None
            papers = []
            if header_line:
                match = re.search(r"Type:\s*(\w+)", header_line)
                if match:
                    node_type = match.group(1)
                papers_match = re.search(r"Papers:\s*([\w\s,_\-]+)", header_line)
                if papers_match:
                    papers = [p.strip() for p in papers_match.group(1).split(",")]

            pages.append(
                {
                    "name": concept_id,
                    "path": md_file.name,
                    "type": node_type,
                    "papers": papers,
                    "updated_at": time.strftime(
                        "%Y-%m-%dT%H:%M:%S", time.localtime(md_file.stat().st_mtime)
                    ),
                }
            )
        except Exception:
            pass
    return pages


def load_page(paper_id: str, concept_id: str) -> str | None:
    """Return raw markdown content of wiki/<concept_id>.md, or None if absent."""
    p = page_path(paper_id, concept_id)
    if p.exists():
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            return None
    return None


def write_page(paper_id: str, concept_id: str, content: str) -> None:
    """Write wiki/<concept_id>.md atomically (tmp → replace)."""
    ensure_wiki_dir(paper_id)
    p = page_path(paper_id, concept_id)
    tmp_p = p.with_suffix(".md.tmp")
    tmp_p.write_text(content, encoding="utf-8")
    os.replace(tmp_p, p)


def rebuild_index(paper_id: str, graph: dict[str, Any]) -> None:
    """Regenerate wiki/index.md from graph nodes. Called after every ingest."""
    ensure_wiki_dir(paper_id)
    nodes = graph.get("nodes", [])
    rows = []
    for node in sorted(nodes, key=lambda n: n.get("label", "")):
        label = node.get("label", "")
        node_type = node.get("type", "concept")
        papers = ", ".join(node.get("papers", []))
        summary = (node.get("summary", "") or "")[:80]
        wiki_page = node.get("wiki_page", f"{node.get('id')}.md")
        rows.append(f"| [{label}]({wiki_page}) | {node_type} | {papers} | {summary} |")

    content = (
        "# Wiki Index\n"
        + f"_Last updated: {time.strftime('%Y-%m-%dT%H:%M:%S')}_\n\n"
        + "## Concepts\n\n"
        + "| Concept | Type | Papers | Summary |\n"
        + "|---------|------|--------|----------|\n"
        + "\n".join(rows)
    )
    index_path = _wiki_dir(paper_id) / "index.md"
    index_path.write_text(content, encoding="utf-8")


def append_log(paper_id: str, entry: str) -> None:
    """Append a timestamped entry to wiki/log.md."""
    ensure_wiki_dir(paper_id)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    log_entry = f"## {timestamp} — {entry}\n\n"

    log_path = _wiki_dir(paper_id) / "log.md"
    if log_path.exists():
        existing = log_path.read_text(encoding="utf-8")
        log_path.write_text(log_entry + existing, encoding="utf-8")
    else:
        log_path.write_text(log_entry, encoding="utf-8")


def resolve_wiki_links(markdown: str) -> str:
    """Convert [[concept_id]] links to markdown links [concept](concept.md).
    Used when serving page content to the frontend so links are clickable."""
    def replace_link(match):
        concept_id = match.group(1)
        return f"[{concept_id}]({concept_id}.md)"

    return re.sub(r"\[\[([a-z0-9_]+)\]\]", replace_link, markdown, flags=re.IGNORECASE)


def _build_page_context(
    node: dict[str, Any],
    graph: dict[str, Any],
    rag_indices: dict[str, "RagIndex"],
    all_papers: dict[str, dict[str, Any]],
    toc_summaries_by_paper: dict[str, dict] | None = None,
) -> str:
    """Assemble context for wiki page generation.

    For each paper in node["papers"]:
      1. Query that paper's RagIndex with node["label"] (top-k=4)
      2. Include relevant toc_summaries for sections where the concept appears
    Also include neighbor node summaries from graph (1-hop adjacency).
    """
    toc_summaries_by_paper = toc_summaries_by_paper or {}
    rag_context_parts = []

    for paper_id in node.get("papers", []):
        rag_idx = rag_indices.get(paper_id)
        if rag_idx:
            try:
                results = rag_idx.topk(node["label"], k=4)
                if results:
                    rag_context_parts.append(f"### {paper_id}\n")
                    for chunk_text in results:
                        rag_context_parts.append(f"- {chunk_text[:300]}...")
            except Exception:
                pass

    toc_context_parts = []
    for paper_id in node.get("papers", []):
        toc_data = toc_summaries_by_paper.get(paper_id)
        if toc_data:
            summaries = toc_data.get("summaries", [])
            for item in summaries:
                title = item.get("title", "")
                summary = item.get("summary", "")
                if node["label"].lower() in (title + summary).lower():
                    toc_context_parts.append(f"- {title}: {summary[:150]}...")

    neighbor_parts = []
    for edge in graph.get("edges", []):
        if edge["source"] == node["id"]:
            neighbor_id = edge["target"]
        elif edge["target"] == node["id"]:
            neighbor_id = edge["source"]
        else:
            continue
        neighbor = next((n for n in graph["nodes"] if n["id"] == neighbor_id), None)
        if neighbor:
            relation = edge.get("relation", "related")
            neighbor_parts.append(f"- [[{neighbor_id}]] ({relation}): {neighbor.get('summary', '')}")

    context = ""
    if rag_context_parts:
        context += "=== RAG Excerpts ===\n" + "\n".join(rag_context_parts) + "\n\n"
    if toc_context_parts:
        context += "=== TOC Summaries ===\n" + "\n".join(toc_context_parts) + "\n\n"
    if neighbor_parts:
        context += "=== Neighbor Concepts ===\n" + "\n".join(neighbor_parts) + "\n"

    return context


def generate_page(
    node: dict[str, Any],
    graph: dict[str, Any],
    rag_indices: dict[str, "RagIndex"],
    all_papers: dict[str, dict[str, Any]],
    toc_summaries_by_paper: dict[str, dict] | None = None,
) -> str:
    """Call wiki_page LLM to generate a concept page. Returns markdown string."""
    system_path = _PROMPTS_DIR / "wiki_page.system.txt"
    system_prompt = system_path.read_text(encoding="utf-8") if system_path.exists() else ""

    user_path = _PROMPTS_DIR / "wiki_page.user.txt"
    user_template = user_path.read_text(encoding="utf-8") if user_path.exists() else ""

    context = _build_page_context(node, graph, rag_indices, all_papers, toc_summaries_by_paper)

    # Format system prompt with template placeholders
    system_prompt = system_prompt.format(
        label=node["label"],
        type=node["type"],
        papers=", ".join(node["papers"]),
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )

    # Format user prompt with template placeholders
    user_prompt = user_template.format(
        label=node["label"],
        concept_id=node["id"],
        type=node["type"],
        papers=", ".join(node["papers"]),
        context=context,
    )

    try:
        response = llm_client.chat(system_prompt, user_prompt)
        return response.strip()
    except Exception as e:
        return f"# {node['label']}\n\n_Error generating page: {e}_"


def update_pages_for_paper(
    paper_id: str,
    affected_node_ids: list[str],
    graph: dict[str, Any],
    rag_indices: dict[str, "RagIndex"],
    all_papers: dict[str, dict[str, Any]],
    toc_summaries_by_paper: dict[str, dict] | None = None,
) -> Iterator[dict[str, Any]]:
    """Generate/update wiki pages for all nodes touched by a paper ingest.

    Yielded records:
      {"stage": "page", "concept_id": "transformer", "msg": "generating..."}
      {"stage": "page_done", "concept_id": "transformer"}
      {"done": True, "pages_written": 14}
    """
    toc_summaries_by_paper = toc_summaries_by_paper or {}
    pages_written = 0

    for concept_id in affected_node_ids:
        node = next((n for n in graph.get("nodes", []) if n["id"] == concept_id), None)
        if not node:
            continue

        yield {"stage": "page", "concept_id": concept_id, "msg": f"Generating wiki page for {node['label']}..."}
        content = generate_page(node, graph, rag_indices, all_papers, toc_summaries_by_paper)
        write_page(paper_id, concept_id, content)
        pages_written += 1
        yield {"stage": "page_done", "concept_id": concept_id}

    yield {"done": True, "pages_written": pages_written}


def regenerate_page(
    paper_id: str,
    concept_id: str,
    graph: dict[str, Any],
    rag_indices: dict[str, "RagIndex"],
    all_papers: dict[str, dict[str, Any]],
    toc_summaries_by_paper: dict[str, dict] | None = None,
) -> str:
    """Regenerate a single page and write it. Returns new content."""
    node = next((n for n in graph.get("nodes", []) if n["id"] == concept_id), None)
    if not node:
        return ""
    content = generate_page(node, graph, rag_indices, all_papers, toc_summaries_by_paper)
    write_page(paper_id, concept_id, content)
    return content


def wiki_qa_stream(
    question: str,
    graph: dict[str, Any],
    rag_indices: dict[str, "RagIndex"],
    history: list[dict],
    paper_id: str,
) -> Iterator[str]:
    """Answer a question using graph + relevant wiki pages as context.

    Strategy:
    1. Find top wiki pages based on node labels matching the question
    2. Load wiki pages for those nodes.
    3. Also run RAG across all loaded papers.
    4. Assemble context, call wiki_qa prompt, stream tokens.
    """
    system_path = _PROMPTS_DIR / "wiki_qa.system.txt"
    system_prompt = system_path.read_text(encoding="utf-8") if system_path.exists() else ""

    user_path = _PROMPTS_DIR / "wiki_qa.user.txt"
    user_template = user_path.read_text(encoding="utf-8") if user_path.exists() else ""

    # Find relevant nodes by simple text matching
    question_lower = question.lower()
    relevant_nodes = [
        n for n in graph.get("nodes", [])
        if question_lower in n.get("label", "").lower() or
           question_lower in n.get("summary", "").lower()
    ]
    # If no exact matches, just take first 5 nodes
    if not relevant_nodes:
        relevant_nodes = graph.get("nodes", [])[:5]
    top_node_ids = [n["id"] for n in relevant_nodes[:5]]

    wiki_pages = []
    for node_id in top_node_ids:
        content = load_page(paper_id, node_id)
        if content:
            wiki_pages.append(f"## {node_id}\n{content}")

    rag_snippets = []
    for rag_idx in rag_indices.values():
        try:
            results = rag_idx.topk(question, k=3)
            for r in results[:3]:
                # Extract text from dict result, or use as string if already text
                text = r.get('text') if isinstance(r, dict) else r
                rag_snippets.append(text)
        except Exception:
            pass

    history_section = ""
    if history:
        history_lines = []
        for msg in history[-2:]:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")
            history_lines.append(f"**{role}**: {content}")
        history_section = "=== Recent conversation ===\n" + "\n".join(history_lines) + "\n=== End conversation ===\n"

    user_prompt = user_template.format(
        history_section=history_section,
        wiki_context="\n".join(wiki_pages) or "No relevant wiki pages found.",
        rag_context="\n".join(rag_snippets) or "No supporting excerpts found.",
        question=question,
    )

    try:
        for chunk in llm_client.stream_messages([{"role": "user", "content": user_prompt}]):
            yield chunk
    except Exception as e:
        yield f"Error: {e}"
