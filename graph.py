"""LLM-driven concept/relationship extraction and graph CRUD."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterator

import llm_client
from config import DATA_DIR

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_MAX_EXTRACT_CHARS = 20000


def _graph_path(paper_id: str) -> Path:
	"""Return the graph.json path for a specific paper."""
	return DATA_DIR / paper_id / "graph.json"


def load_graph(paper_id: str) -> dict[str, Any]:
    """Load graph.json for a specific paper. Returns empty graph skeleton if file absent."""
    graph_path = _graph_path(paper_id)
    if graph_path.exists():
        return json.loads(graph_path.read_text(encoding="utf-8"))
    return _empty_graph()


def save_graph(graph: dict[str, Any], paper_id: str) -> None:
    """Atomic write via .tmp → os.replace, same pattern as toc_summary.save_cache."""
    graph_path = _graph_path(paper_id)
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = graph_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, graph_path)


def _empty_graph() -> dict[str, Any]:
    """Return the canonical empty graph skeleton."""
    return {
        "version": 1,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "nodes": [],
        "edges": [],
    }


def _canonical_id(label: str) -> str:
    """Convert 'Multi-Head Attention' -> 'multi_head_attention'.
    Lowercases, replaces spaces/hyphens with underscore, strips punctuation."""
    s = label.lower().strip()
    s = re.sub(r"['\"]", "", s)
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    s = re.sub(r"_+", "_", s)
    s = s.strip("_")
    return s


def _find_existing_node(
    graph: dict, candidate_id: str, candidate_aliases: list[str]
) -> str | None:
    """Return the id of an existing node that matches by id OR alias overlap.
    Used to detect 'attention mechanism' == 'attention' before inserting a duplicate."""
    if not candidate_aliases:
        candidate_aliases = []

    for node in graph.get("nodes", []):
        if node["id"] == candidate_id:
            return node["id"]
        node_aliases = [alias.lower() for alias in (node.get("aliases") or [])]
        candidate_aliases_lower = [alias.lower() for alias in candidate_aliases]
        if set(node_aliases) & set(candidate_aliases_lower):
            return node["id"]
    return None


def _build_extract_prompt(
    paper_id: str, pages_text: str, toc_summaries: dict | None
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) with full context for concept extraction.
    Combines paginated markdown (truncated to _MAX_EXTRACT_CHARS) with toc_summaries
    for broader section-level understanding."""
    system_path = _PROMPTS_DIR / "graph_extract.system.txt"
    system_prompt = system_path.read_text(encoding="utf-8") if system_path.exists() else ""

    user_path = _PROMPTS_DIR / "graph_extract.user.txt"
    user_template = user_path.read_text(encoding="utf-8") if user_path.exists() else ""

    pages_text = pages_text[: int(_MAX_EXTRACT_CHARS * 1.5)]

    toc_section = ""
    if toc_summaries:
        toc_lines = []
        for item in toc_summaries.get("summaries", []):
            title = item.get("title", "")
            summary = item.get("summary", "")
            if title and summary:
                toc_lines.append(f"### {title}\n{summary}")
        if toc_lines:
            toc_section = "=== TOC Summaries (high-level structure) ===\n" + "\n\n".join(
                toc_lines
            ) + "\n=== End TOC Summaries ===\n"

    user_prompt = user_template.format(
        paper_id=paper_id,
        toc_section=toc_section,
        pages_text=pages_text,
    )

    return system_prompt, user_prompt


def _parse_extraction(raw: str) -> dict[str, Any]:
    """Parse LLM JSON output into {"nodes": [...], "edges": [...]}.
    Tolerates markdown code fences. Returns empty dict on parse failure."""
    raw = raw.strip()
    if raw.startswith("```json"):
        raw = raw[7:]
    if raw.startswith("```"):
        raw = raw[3:]
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        result = json.loads(raw)
        return result
    except (json.JSONDecodeError, ValueError):
        return {}


def extract_concepts(
    paper_id: str,
    pages: list[dict[str, Any]],
    toc_summaries: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call the LLM to extract concepts and relationships from a paper.

    Strategy: chunk the full paper markdown into ≤_MAX_EXTRACT_CHARS segments,
    run one extraction call per chunk, then merge results. For typical ML papers
    (≤30 pages × ~800 chars/page = ~24K chars) this is usually a single call.

    Returns raw extraction dict: {"nodes": [...], "edges": [...]}
    Each node: {"label": str, "type": str, "summary": str, "aliases": [str]}
    Each edge: {"source_label": str, "target_label": str, "relation": str}
    """
    pages_text = ""
    for page in pages:
        if isinstance(page, dict):
            markdown = page.get("markdown", "")
            if markdown:
                pages_text += markdown + "\n\n"
        elif isinstance(page, str):
            pages_text += page + "\n\n"

    system_prompt, user_prompt = _build_extract_prompt(paper_id, pages_text, toc_summaries)

    try:
        response = llm_client.chat(system_prompt, user_prompt)
        result = _parse_extraction(response)
        if result:
            return result
    except Exception as e:
        print(f"[graph] extraction LLM error: {e}")

    return {"nodes": [], "edges": []}


def merge_extraction_into_graph(
    graph: dict[str, Any],
    extraction: dict[str, Any],
    paper_id: str,
) -> dict[str, str]:
    """Merge extracted nodes/edges into the graph in-place.

    Deduplication logic:
    1. Compute canonical_id for each extracted node label.
    2. Check if canonical_id already exists in graph.nodes (exact match).
    3. If not, check alias overlap via _find_existing_node.
    4. If match found: update .papers list (append paper_id if absent), merge aliases.
    5. If no match: insert as new node.
    6. For edges: resolve source/target labels to canonical ids, upsert edge,
       increment weight if same (source, target, relation) triple already exists.

    Returns id_map: {extracted_label -> resolved_canonical_id} for use by wiki.py.
    """
    id_map = {}

    extracted_nodes = extraction.get("nodes", [])
    extracted_edges = extraction.get("edges", [])

    for extracted_node in extracted_nodes:
        label = extracted_node.get("label", "").strip()
        if not label:
            continue

        node_type = extracted_node.get("type", "concept")
        node_summary = extracted_node.get("summary", "")
        node_aliases = extracted_node.get("aliases", []) or []

        canonical_id = _canonical_id(label)
        existing_id = _find_existing_node(graph, canonical_id, node_aliases)

        if existing_id:
            id_map[label] = existing_id
            node_to_update = next((n for n in graph["nodes"] if n["id"] == existing_id), None)
            if node_to_update:
                if paper_id not in node_to_update.get("papers", []):
                    node_to_update["papers"].append(paper_id)
                for alias in node_aliases:
                    if alias not in node_to_update.get("aliases", []):
                        node_to_update["aliases"].append(alias)
        else:
            new_node = {
                "id": canonical_id,
                "label": label,
                "type": node_type,
                "aliases": node_aliases,
                "papers": [paper_id],
                "summary": node_summary,
                "wiki_page": f"{canonical_id}.md",
                "added_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            graph["nodes"].append(new_node)
            id_map[label] = canonical_id

    for extracted_edge in extracted_edges:
        source_label = extracted_edge.get("source_label", "").strip()
        target_label = extracted_edge.get("target_label", "").strip()
        relation = extracted_edge.get("relation", "").strip() or "related"

        if not (source_label and target_label):
            continue

        source_id = id_map.get(source_label)
        target_id = id_map.get(target_label)

        if not (source_id and target_id):
            continue

        existing_edge = next(
            (
                e
                for e in graph["edges"]
                if e["source"] == source_id and e["target"] == target_id and e["relation"] == relation
            ),
            None,
        )

        if existing_edge:
            existing_edge["weight"] = existing_edge.get("weight", 1) + 1
            if paper_id not in existing_edge.get("papers", []):
                existing_edge["papers"].append(paper_id)
        else:
            new_edge = {
                "source": source_id,
                "target": target_id,
                "relation": relation,
                "weight": 1,
                "papers": [paper_id],
            }
            graph["edges"].append(new_edge)

    return id_map


def ingest_paper(
    paper_id: str,
    pages: list[dict[str, Any]],
    toc_summaries: dict[str, Any] | None = None,
) -> Iterator[dict[str, Any]]:
    """Full pipeline: extract -> merge -> save graph. Yields NDJSON progress records."""
    yield {"stage": "extracting", "msg": "Calling LLM for concept extraction..."}

    extraction = extract_concepts(paper_id, pages, toc_summaries)
    n_nodes = len(extraction.get("nodes", []))
    n_edges = len(extraction.get("edges", []))
    yield {"stage": "extracted", "nodes": n_nodes, "edges": n_edges}

    yield {"stage": "merging", "msg": "Merging into graph..."}
    graph = load_graph(paper_id)
    old_node_count = len(graph["nodes"])
    id_map = merge_extraction_into_graph(graph, extraction, paper_id)
    new_node_count = len(graph["nodes"])

    yield {
        "stage": "merged",
        "new_nodes": new_node_count - old_node_count,
        "updated_nodes": len([n for n in graph["nodes"] if paper_id in n.get("papers", [])]),
        "new_edges": len(graph["edges"]),
    }

    yield {"stage": "saving", "msg": "Saving graph.json..."}
    graph["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_graph(graph, paper_id)
    yield {"stage": "saved", "msg": "graph.json written"}

    yield {
        "done": True,
        "ok": True,
        "paper_id": paper_id,
        "new_nodes": new_node_count - old_node_count,
        "total_nodes": new_node_count,
        "id_map": id_map,
    }


def get_graph(paper_id: str) -> dict[str, Any]:
    """Public accessor used by serve.py GET /api/wiki/graph."""
    return load_graph(paper_id)
