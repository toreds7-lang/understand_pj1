"""Agentic-RAG orchestration over a per-paper Microsoft GraphRAG backend.

The agentic loop is the brain; GraphRAG's four retrieval methods (global / local /
drift / basic) are its tools:

    Planner (sub-question + method each) -> Search Fanout (call GraphRAG per item)
            -> Sufficient-Context gate -> (loop back with follow-up {q, method})
            -> Synthesis (cited by method)

Ported from gemini_rag/agents.py. Differences for this app:
  * Retrieval is delegated to a per-paper `GraphRAGQA` engine passed in by the caller
    (this app swaps the "current paper"), not a module-global singleton.
  * Uses this project's `llm_client` (chat_json + stream_messages).
  * Adds `stream_with_trace()` which folds the live agent trace into the same plain-text
    stream as the answer, so the existing chat UI shows progress with no JS changes.
"""
from __future__ import annotations

import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import hybrid_retrieval as hybrid
import llm_client
from config import MAX_ITERS, MAX_SNIPPETS
from graphrag_qa import GraphRAGQA, METHODS

# Prompts are kept external/editable next to the exe (like ai.py), not bundled into the
# PyInstaller _MEIPASS temp dir — so resolve from the exe dir when frozen.
_PROMPTS = (Path(sys.executable).parent if getattr(sys, "frozen", False)
            else Path(__file__).parent) / "prompts"

# Escalation order used when the gate proposes a follow-up without naming a method:
# move to a strictly more powerful tool so the next pass tries something different.
_ESCALATE = {"basic": "local", "local": "drift", "drift": "global", "global": "global"}


class GraphRAGUnavailable(RuntimeError):
    """Raised when a GraphRAG search fails for an item (degrades to empty evidence)."""


# GraphRAG's global/local/drift/basic searches each return a *synthesized answer*, not raw
# chunks — so when a method finds nothing it still returns a non-empty "I can't answer from
# this data" string. Unlike the vector retriever the reference apps use (which just returns
# the nearest chunks), that refusal would otherwise be kept as evidence, OUTSCORE real
# answers (score is rank-based, not content-based), satisfy the gate, and get parroted by
# synthesis — which is exactly how a question fails with "the retrieved evidence does not
# contain…". Detect and drop these non-answers so the loop escalates/falls back instead.
_NONANSWER_RE = re.compile(
    r"i\s*(?:'?a?m)?\s+sorry|unable\s+to\s+answer|cannot\s+answer|can'?t\s+answer|"
    r"do(?:es)?\s+not\s+contain\s+(?:any\s+)?(?:information|relevant|details?)|"
    r"does\s+not\s+(?:provide|mention|include|discuss)|no\s+(?:relevant\s+)?information|"
    r"not\s+enough\s+information|do(?:\s+not|n'?t)\s+have\s+(?:enough\s+)?(?:information|details?)|"
    r"(?:provided|retrieved)\s+(?:data|evidence|context|text|information)\s+does\s+not",
    re.IGNORECASE,
)
# Only short, refusal-dominated responses are non-answers; a long answer that merely notes
# a gap in passing should still count as evidence.
_NONANSWER_MAX_CHARS = 700


def _is_nonanswer(text: str) -> bool:
    """True if a GraphRAG response is a 'no information / cannot answer' refusal rather than
    a real answer, so it should be dropped instead of used as evidence."""
    t = (text or "").strip()
    return bool(t) and len(t) <= _NONANSWER_MAX_CHARS and _NONANSWER_RE.search(t) is not None


def _prompt(name: str) -> str:
    return (_PROMPTS / f"{name}.system.txt").read_text(encoding="utf-8").strip()


def _norm_method(m: Any) -> str:
    """Coerce a planner/gate-supplied method to a valid tool, defaulting to 'auto'."""
    m = str(m or "").lower().strip()
    return m if m in METHODS or m == "auto" else "auto"


def _norm_item(item: Any) -> dict[str, str] | None:
    """Coerce one planned step into {'q', 'method'}; tolerate a bare string."""
    if isinstance(item, str):
        q = item.strip()
        return {"q": q, "method": "auto"} if q else None
    if isinstance(item, dict):
        q = str(item.get("q") or item.get("question") or "").strip()
        return {"q": q, "method": _norm_method(item.get("method"))} if q else None
    return None


# ---------------------------------------------------------------------------
# GraphRAG bridge (per-paper engine)
# ---------------------------------------------------------------------------

def _ask_graphrag(engine: GraphRAGQA, question: str, method: str = "auto") -> dict:
    """Run one GraphRAG search via the given engine; return {"text","method","routed"}."""
    m = method.lower().strip()
    if m not in METHODS and m != "auto":
        m = "auto"
    try:
        ans = engine.ask(question, method=m)
    except Exception as e:  # noqa: BLE001 — degrade this item, don't crash the loop
        raise GraphRAGUnavailable(f"GraphRAG search failed ({m}): {e}") from e
    return {"text": ans.text, "method": ans.method, "routed": ans.routed}


# ---------------------------------------------------------------------------
# Document-structure lookup (corpus sections)
# ---------------------------------------------------------------------------
# GraphRAG indexes entities/communities and vector chunks; it has no concept of a
# named section ("the abstract", "the conclusion"). Asking it to "summarize the
# abstract" therefore misses: global returns generic theme text and basic can't
# semantically match a meta-query. For section-targeted questions we instead pull the
# section verbatim from the built corpus (input/*.txt) and feed it as evidence.

# Canonical section -> question keywords that should trigger a direct lookup.
_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "abstract": ("abstract",),
    "introduction": ("introduction", "intro"),
    "background": ("background",),
    "related work": ("related work", "related works", "prior work"),
    "method": ("methodology", "method", "methods", "approach"),
    "evaluation": ("evaluation", "experiment", "experiments", "experimental"),
    "results": ("results", "findings"),
    "discussion": ("discussion",),
    "conclusion": ("conclusion", "conclusions", "concluding"),
    "references": ("references", "bibliography"),
}

_SECTION_MAX_CHARS = 6000


def _corpus_text(engine: GraphRAGQA) -> str:
    """Read the per-paper corpus dumped at <root>/input/*.txt (best-effort)."""
    try:
        files = sorted((engine.root / "input").glob("*.txt"))
        return files[0].read_text(encoding="utf-8") if files else ""
    except Exception:  # noqa: BLE001 — section lookup is an optional enhancement
        return ""


def _norm_heading(title: str) -> str:
    """Strip markdown and any leading section number from a heading -> lowercase title."""
    t = re.sub(r"[#*_`]", "", title)                                    # markdown marks
    t = re.sub(r"^\s*(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)\.?\s+", "", t)   # leading number
    return t.strip().lower()


# A section header is either a markdown '#'-heading line, or a bold-label line like
# "**Abstract** —body…" / "**Index Terms** —…". Two-column papers (and many PDF->markdown
# conversions) render the abstract not as a '#' heading but as a leading bold label with
# the body inline on the SAME line, so '#'-only parsing silently drops it.
_HEADING_RE = re.compile(r"^\s*#+\s*(.+?)\s*$")
_BOLDLABEL_RE = re.compile(r"^\s*\*\*\s*([^*\n]+?)\s*\*\*\s*[—–\-:.]*\s*(.*)$")


def _parse_sections(text: str) -> list[tuple[str, str]]:
    """Split corpus into [(normalized_title, body)] by heading or bold-label lines, in
    order. For a bold label the body begins inline on the label's own line."""
    lines = text.splitlines()
    heads: list[tuple[int, str, str]] = []  # (line_idx, normalized_title, inline_body)
    for i, ln in enumerate(lines):
        if m := _HEADING_RE.match(ln):
            heads.append((i, _norm_heading(m.group(1)), ""))
        elif (m := _BOLDLABEL_RE.match(ln)) and len(m.group(1).split()) <= 5:
            # Cap the label at ~5 words so a sentence that merely starts in bold isn't
            # mistaken for a heading and used to fragment a real section body.
            heads.append((i, _norm_heading(m.group(1)), m.group(2).strip()))
    sections: list[tuple[str, str]] = []
    for j, (idx, title, inline) in enumerate(heads):
        end = heads[j + 1][0] if j + 1 < len(heads) else len(lines)
        rest = "\n".join(ln for ln in lines[idx + 1:end]
                         if not re.match(r"^\s*\[page \d+\]\s*$", ln)).strip()
        body = "\n".join(p for p in (inline, rest) if p).strip()
        if title and body:
            sections.append((title, body))
    return sections


def _heading_matches(title: str, aliases: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(a)}\b", title) for a in aliases)


def section_evidence(engine: GraphRAGQA, questions: list[str]) -> list[dict]:
    """Direct evidence blocks for any document section named in `questions`.

    Returns at most one block per matched section, scored high so it survives the
    MAX_SNIPPETS merge and ranks first for synthesis.
    """
    qs = " \n ".join(q.lower() for q in questions if q)
    wanted = [canon for canon, aliases in _SECTION_ALIASES.items()
              if any(re.search(rf"\b{re.escape(a)}\b", qs) for a in aliases)]
    if not wanted:
        return []
    sections = _parse_sections(_corpus_text(engine))
    if not sections:
        return []
    out: list[dict] = []
    used: set[str] = set()
    for canon in wanted:
        for title, body in sections:
            if title in used or not _heading_matches(title, _SECTION_ALIASES[canon]):
                continue
            used.add(title)
            out.append({
                "source": "corpus/section",
                "method": "section",
                "query": f"{canon} section",
                "text": body[:_SECTION_MAX_CHARS],
                "score": 1000.0,
            })
            break
    return out


def list_documents(engine: GraphRAGQA) -> tuple[str, ...]:
    """Titles of documents in the built index, so the planner knows the corpus."""
    try:
        import pandas as pd
        docs = pd.read_parquet(engine.root / "output" / "documents.parquet")
        col = "title" if "title" in docs.columns else docs.columns[0]
        return tuple(str(t) for t in docs[col].tolist())
    except Exception:  # noqa: BLE001 — planner grounding is best-effort
        return ()


# ---------------------------------------------------------------------------
# Raw-corpus fallback (LLM-free)
# ---------------------------------------------------------------------------
# When every GraphRAG method comes back empty/refusal for a question, we still want REAL
# document text to synthesize from — the way the vector-RAG reference apps always return
# their nearest chunks instead of refusing. We retrieve that text straight from the per-paper
# corpus by simple keyword overlap (no embeddings, no LLM call), so it stays reliable even
# when the local LLM is flaky. This is the generic counterpart to section_evidence (which
# only handles named sections); together they keep the agent from ever giving up on a
# question whose answer is plainly in the document. Shares the keyword tokenizer with the
# hybrid retriever.

_FALLBACK_CHUNK_CHARS = 1600
_FALLBACK_TOP_K = 4


def _corpus_chunks(text: str) -> list[str]:
    """Split the corpus into ~paragraph chunks, dropping [page N] markers. Over-long
    paragraphs are sliced so one huge block can't dominate ranking."""
    body = "\n".join(ln for ln in text.splitlines()
                     if not re.match(r"^\s*\[page \d+\]\s*$", ln))
    chunks: list[str] = []
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if len(para) < 60:  # skip page-number digits, lone headings, separators ("✦")
            continue
        for i in range(0, len(para), _FALLBACK_CHUNK_CHARS):
            chunks.append(para[i:i + _FALLBACK_CHUNK_CHARS])
    return chunks


def corpus_fallback(engine: GraphRAGQA, question: str, k: int = _FALLBACK_TOP_K) -> list[dict]:
    """Top-k raw corpus chunks most relevant to `question` by keyword overlap (no LLM).

    A reliability net mirroring the reference apps: when GraphRAG yields no usable evidence,
    synthesize from actual document text rather than giving up. With no lexical overlap
    (e.g. a bare "summarize"), fall back to the opening chunks — title/abstract/intro are
    the most representative generic context.
    """
    chunks = _corpus_chunks(_corpus_text(engine))
    if not chunks:
        return []
    qterms = set(hybrid.content_terms(question))
    scored: list[tuple[float, str]] = []
    for ch in chunks:
        cterms = hybrid.content_terms(ch)
        present = qterms.intersection(cterms)
        if not present:
            continue
        # Reward distinct query terms matched, lightly weighted by their frequency.
        scored.append((len(present) + 0.1 * sum(cterms.count(t) for t in present), ch))
    scored.sort(key=lambda x: x[0], reverse=True)
    picked = scored[:k] if scored else [(0.0, ch) for ch in chunks[:k]]
    return [{
        "source": "corpus/fallback",
        "method": "corpus",
        "query": question,
        "text": ch[:_SECTION_MAX_CHARS],
        "score": 100.0 - rank,  # ordered; only ever used when no other evidence exists
    } for rank, (_score, ch) in enumerate(picked)]


# ---------------------------------------------------------------------------
# Hybrid raw-chunk retrieval (vector + keyword), always-on alongside graph
# ---------------------------------------------------------------------------
# One HybridRetriever per built index, cached by root (matches graphrag_manager's engine
# cache). Built lazily so constructing it never blocks chat; any failure degrades to no
# hybrid evidence (graph still answers).

_retrievers: dict[str, hybrid.HybridRetriever] = {}


def _retriever_for(engine: GraphRAGQA) -> hybrid.HybridRetriever | None:
    key = str(engine.root)
    r = _retrievers.get(key)
    if r is not None and r.stale():  # index rebuilt under us — drop the stale instance
        _retrievers.pop(key, None)
        r = None
    if r is None:
        try:
            r = hybrid.HybridRetriever(engine.root)
        except Exception as e:  # noqa: BLE001 — hybrid is an enhancement, never block chat
            print(f"[hybrid] retriever unavailable for {key}: {e}", file=sys.stderr)
            return None
        _retrievers[key] = r
    return r


def hybrid_evidence(engine: GraphRAGQA, queries: list[str]) -> list[dict]:
    """Always-on raw-chunk evidence (vector+keyword, RRF-fused) for the planned queries.
    Down-weighted below graph answers so graph stays primary; never raises."""
    r = _retriever_for(engine)
    if r is None:
        return []
    try:
        return r.retrieve(queries)
    except Exception as e:  # noqa: BLE001 — degrade to graph-only on any retriever error
        print(f"[hybrid] retrieval failed: {e}", file=sys.stderr)
        return []


def hybrid_fanout(items: list[dict[str, str]], engine: GraphRAGQA) -> list[dict]:
    """Hybrid-only counterpart to search_fanout: pull vector+keyword raw chunks for each
    planned sub-question instead of calling GraphRAG. Used when the agentic loop runs with
    graph search excluded (use_graph=False)."""
    queries = [it["q"] for it in items if it.get("q")]
    return hybrid_evidence(engine, queries)


# ---------------------------------------------------------------------------
# Evidence bookkeeping
# ---------------------------------------------------------------------------

def _evidence_key(e: dict[str, Any]) -> tuple[str, str]:
    """Identity for dedup: same source method + same query is the same evidence."""
    return (str(e.get("source", "")), str(e.get("query", "")))


def _merge_evidence(existing: list[dict], new: list[dict]) -> list[dict]:
    """Union existing + new evidence, keeping the higher score on collision, then
    return the MAX_SNIPPETS best-scored, sorted by descending score."""
    by_key: dict[tuple[str, str], dict] = {}
    for e in existing + new:
        k = _evidence_key(e)
        if k not in by_key or e.get("score", 0.0) > by_key[k].get("score", 0.0):
            by_key[k] = e
    merged = sorted(by_key.values(), key=lambda e: e.get("score", 0.0), reverse=True)
    return merged[:MAX_SNIPPETS]


def _render_evidence(evidence: list[dict]) -> str:
    """Format evidence blocks as a numbered, method-tagged block for an LLM prompt."""
    if not evidence:
        return "(no evidence retrieved)"
    lines = []
    for i, e in enumerate(evidence, 1):
        method = str(e.get("method") or e.get("source", "")).split("/")[-1] or "?"
        query = str(e.get("query", "")).strip()
        head = f"[{i}] (via {method})" + (f"  query: {query}" if query else "")
        lines.append(f"{head}\n{str(e.get('text', '')).strip()}")
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Individual agents
# ---------------------------------------------------------------------------

def plan(question: str, engine: GraphRAGQA) -> dict[str, Any]:
    """Planner: decompose the question into focused {sub-question, method} steps."""
    docs = list_documents(engine)
    corpus = ""
    if docs:
        listing = "\n".join(f"- {d}" for d in docs)
        corpus = (f"The corpus contains exactly {len(docs)} document(s):\n{listing}\n\n"
                  "Treat this list as authoritative — do not invent or assume any other "
                  "documents.\n\n")
    out = llm_client.chat_json(_prompt("planner"), f"{corpus}Question: {question}")
    items: list[dict[str, str]] = []
    if isinstance(out, dict) and isinstance(out.get("subquestions"), list):
        items = [it for it in (_norm_item(x) for x in out["subquestions"]) if it]
    elif isinstance(out, list):  # tolerate a bare JSON array
        items = [it for it in (_norm_item(x) for x in out) if it]
    if not items:  # planner fallback: ask the whole question, let GraphRAG route it
        return {"reasoning": "(planner fallback)",
                "subquestions": [{"q": question, "method": "auto"}]}
    reasoning = str(out.get("reasoning", "")) if isinstance(out, dict) else ""
    return {"reasoning": reasoning, "subquestions": items}


def search_fanout(items: list[dict[str, str]], engine: GraphRAGQA) -> list[dict]:
    """Search Fanout: run each {q, method} through GraphRAG and collect evidence.

    Searches run concurrently (each in its own asyncio loop in a worker thread). A
    failed call degrades to an empty result for that item rather than crashing.
    """
    def run(item: dict[str, str], rank: int) -> dict | None:
        try:
            res = _ask_graphrag(engine, item["q"], item["method"])
        except GraphRAGUnavailable:
            return None
        if not res.get("text") or _is_nonanswer(res["text"]):
            return None
        return {
            "source": f"graphrag/{res['method']}",
            "method": res["method"],
            "query": item["q"],
            "text": res["text"],
            # Graph answers occupy a band above hybrid raw chunks (so graph stays primary)
            # and below section_evidence; earlier items rank higher for a stable order.
            "score": 500.0 + (len(items) - rank),
        }

    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(items))) as ex:
        results = list(ex.map(lambda p: run(*p), [(it, i) for i, it in enumerate(items)]))
    return _merge_evidence([], [r for r in results if r])


def assess_sufficiency(question: str, evidence: list[dict]) -> dict[str, Any]:
    """Sufficient-Context gate: judge whether evidence answers the question and, if
    not, name what is missing and propose follow-up {q, method} retrievals."""
    user = f"Question: {question}\n\nRetrieved evidence:\n{_render_evidence(evidence)}"
    out = llm_client.chat_json(_prompt("sufficient_context"), user)
    if not isinstance(out, dict):
        # If the gate fails to parse, assume sufficient so we synthesize rather than spin.
        return {"sufficient": True, "draft": "", "missing": [], "followup_queries": []}
    followups = [it for it in (_norm_item(x) for x in (out.get("followup_queries") or [])) if it]
    return {
        "sufficient": bool(out.get("sufficient", True)),
        "draft": str(out.get("draft", "")),
        "missing": [str(m) for m in (out.get("missing") or [])],
        "followup_queries": followups,
    }


def _escalate(items: list[dict[str, str]], prior: list[dict[str, str]]) -> list[dict[str, str]]:
    """For follow-ups left as 'auto', pick a stronger method than the prior pass used."""
    prior_methods = {it["method"] for it in prior}
    base = max(prior_methods, key=lambda m: list(_ESCALATE).index(m)
               if m in _ESCALATE else -1) if prior_methods else "local"
    out = []
    for it in items:
        method = it["method"] if it["method"] != "auto" else _ESCALATE.get(base, "global")
        out.append({"q": it["q"], "method": method})
    return out


def synthesize(question: str, evidence: list[dict]) -> Iterator[str]:
    """Synthesis: stream the final grounded, method-cited answer."""
    user = f"Question: {question}\n\nRetrieved evidence:\n{_render_evidence(evidence)}"
    messages = [
        {"role": "system", "content": _prompt("synthesis")},
        {"role": "user", "content": user},
    ]
    yield from llm_client.stream_messages(messages)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class IterationTrace:
    index: int
    items: list[dict[str, str]]
    n_evidence: int
    sufficient: bool
    missing: list[str] = field(default_factory=list)
    followup_queries: list[dict[str, str]] = field(default_factory=list)


TraceCb = Callable[[str, Any], None]


def run_agentic_rag(
    question: str,
    engine: GraphRAGQA,
    trace_cb: TraceCb | None = None,
    use_graph: bool = True,
) -> Iterator[str]:
    """Run the full agentic-RAG loop and stream the synthesized answer token-by-token.

    Stage progress is reported through `trace_cb(stage, payload)` (stages: "plan",
    "iteration", "synthesis_start"); pass None to run quietly.

    use_graph=False keeps the same plan -> search -> sufficiency-gate -> synthesis loop but
    replaces GraphRAG search with hybrid (vector+keyword) retrieval, for the "no graph" mode.
    """
    def emit(stage: str, payload: Any) -> None:
        if trace_cb is not None:
            trace_cb(stage, payload)

    p = plan(question, engine)
    emit("plan", p)

    items: list[dict[str, str]] = p["subquestions"]
    queries = [question] + [it["q"] for it in items]
    # Seed evidence with raw document text BEFORE any graph search, so answers never hang
    # solely on GraphRAG retrieval: named-section text for structural questions, plus an
    # always-on hybrid (vector+keyword) chunk pull — down-weighted so graph stays primary.
    evidence: list[dict] = _merge_evidence(
        section_evidence(engine, queries), hybrid_evidence(engine, queries))
    for i in range(MAX_ITERS):
        fresh = search_fanout(items, engine) if use_graph else hybrid_fanout(items, engine)
        evidence = _merge_evidence(evidence, fresh)
        verdict = assess_sufficiency(question, evidence)
        emit("iteration", IterationTrace(
            index=i, items=items, n_evidence=len(evidence),
            sufficient=verdict["sufficient"], missing=verdict["missing"],
            followup_queries=verdict["followup_queries"],
        ))
        if verdict["sufficient"] or not verdict["followup_queries"]:
            break
        items = _escalate(verdict["followup_queries"], items) if use_graph else verdict["followup_queries"]

    if not evidence:  # every GraphRAG method came back empty/refusal — ground on raw text
        evidence = corpus_fallback(engine, question)
    emit("synthesis_start", len(evidence))
    yield from synthesize(question, evidence)


# ---------------------------------------------------------------------------
# Trace-as-text streaming (for the plain-text chat UI)
# ---------------------------------------------------------------------------

def _fmt_methods(items: list[dict[str, str]]) -> str:
    return ", ".join(it["method"] for it in items)


def stream_with_trace(question: str, engine: GraphRAGQA, use_graph: bool = True) -> Iterator[str]:
    """Like run_agentic_rag, but folds a compact markdown trace into the SAME text
    stream as the answer, so the existing chat panel renders live progress.

    use_graph=False runs the identical plan -> search -> sufficiency-gate -> synthesis loop
    with graph search excluded: every search step uses hybrid (vector+keyword) retrieval only.
    """
    p = plan(question, engine)
    subs = p["subquestions"]
    yield "**🧭 Planner**\n"
    if p.get("reasoning"):
        yield f"> {p['reasoning']}\n"
    for it in subs:
        yield f"- {it['q']}" + (f"  _[{it['method']}]_\n" if use_graph else "\n")
    yield "\n"

    items = subs
    queries = [question] + [it["q"] for it in subs]
    sections = section_evidence(engine, queries)
    if sections:
        names = ", ".join(sorted({e["query"] for e in sections}))
        yield f"**📑 Section lookup** — pulled {names} directly from the document\n\n"
    hits = hybrid_evidence(engine, queries)
    if hits:
        how = hits[0].get("query", "keyword")
        tail = "alongside graph search" if use_graph else "(graph search excluded)"
        yield f"**🔀 Hybrid retrieval** — added {len(hits)} raw passage(s) ({how}) {tail}\n\n"
    evidence: list[dict] = _merge_evidence(sections, hits)
    for i in range(MAX_ITERS):
        label = f"methods: {_fmt_methods(items)}" if use_graph else "vector+keyword retrieval"
        yield f"**🔎 Search + Sufficiency** _(iteration {i + 1})_ — {label}\n"
        fresh = search_fanout(items, engine) if use_graph else hybrid_fanout(items, engine)
        evidence = _merge_evidence(evidence, fresh)
        verdict = assess_sufficiency(question, evidence)
        state = "sufficient ✅" if verdict["sufficient"] else "insufficient ↻"
        yield f"> {len(evidence)} evidence block(s) — {state}\n"
        if not verdict["sufficient"]:
            for m in verdict["missing"]:
                yield f"> missing: {m}\n"
        yield "\n"
        if verdict["sufficient"] or not verdict["followup_queries"]:
            break
        items = _escalate(verdict["followup_queries"], items) if use_graph else verdict["followup_queries"]

    if not evidence:  # every search method came back empty/refusal — ground on raw text
        evidence = corpus_fallback(engine, question)
        if evidence:
            yield (f"**📄 Corpus fallback** — search found nothing usable; grounding on "
                   f"{len(evidence)} raw passage(s) pulled directly from the document\n\n")

    yield f"**🧩 Synthesis** _(grounding on {len(evidence)} block(s))_\n\n---\n\n"
    yield from synthesize(question, evidence)
