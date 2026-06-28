"""Generate a self-contained, multi-depth lecture HTML from a parsed paper.

Reuses the TOC tree (toc_summary.build_tree) for module structure and page
ranges, the cached section summaries (toc_summaries.json) to seed each module,
and the paper's extracted figures. One structured LLM call per chapter produces
all three depths at once. The build is resumable: re-running only regenerates
modules that are missing or errored (mirrors toc_summary.summarize_node)."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import llm_client
import toc_summary

_PROMPTS_DIR = (Path(sys.executable).parent if getattr(sys, "frozen", False)
                else Path(__file__).parent) / "prompts"
_TEMPLATE = (Path(sys.executable).parent if getattr(sys, "frozen", False)
             else Path(__file__).parent) / "lecture_template.html"

_CACHE_NAME = "lecture.json"
_HTML_NAME = "lecture.html"
_MAX_CHARS = 16000
_ERR_PREFIX = "[lecture error:"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


# ─── paths / cache I/O ──────────────────────────────────────────────────────

def _dir(data_dir: Path) -> Path:
    return data_dir / "lecture"


def cache_path(data_dir: Path) -> Path:
    return _dir(data_dir) / _CACHE_NAME


def html_path(data_dir: Path) -> Path:
    return _dir(data_dir) / _HTML_NAME


def load_cache(data_dir: Path) -> dict[str, Any]:
    p = cache_path(data_dir)
    if not p.exists():
        return {"modules": {}, "meta": {}, "stages": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("modules", {})
        data.setdefault("meta", {})
        data.setdefault("stages", {})
        return data
    except Exception as exc:
        print(f"[lecture] cache read failed: {exc}", file=sys.stderr)
        return {"modules": {}, "meta": {}, "stages": {}}


def save_cache(data_dir: Path, cache: dict[str, Any]) -> None:
    p = cache_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


# ─── module skeletons (no LLM) ──────────────────────────────────────────────

def _figures_in_range(pages: list[dict[str, Any]], a: int, b: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for i in range(a, b + 1):
        if not (0 <= i < len(pages)):
            continue
        for f in (pages[i].get("figures") or []):
            path = f.get("path") or ""
            name = os.path.basename(path)
            if not name:
                continue
            out.append({"src": f"/figures/{name}", "cap": (f.get("caption") or "").strip()})
    return out


def build_modules(toc_roots: list, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Top-level TOC nodes become chapter modules, in order. Pure structure.

    A chapter spans from its own start page up to the page before the next
    top-level chapter (build_tree only records the intro page on a parent, so we
    recompute the full span here to capture all of the chapter's subsections)."""
    n_pages = len(pages)
    mods: list[dict[str, Any]] = []
    for idx, node in enumerate(toc_roots, start=1):
        a = node.page_start
        if idx < len(toc_roots):
            b = max(a, toc_roots[idx].page_start - 1)
        else:
            b = max(a, n_pages - 1)
        b = min(b, n_pages - 1)
        mods.append({
            "n": f"c{idx:02d}",
            "anchor": node.anchor,
            "title": node.title,
            "layer": f"pp. {a + 1}–{b + 1}",
            "page_start": a,
            "page_end": b,
            "figs": _figures_in_range(pages, a, b),
            "_node": node,
        })
    return mods


# ─── per-module generation (LLM) ────────────────────────────────────────────

def _module_input(node, pages: list[dict[str, Any]], toc_cache: dict,
                  a: int, b: int) -> str:
    parts: list[str] = []
    for i in range(a, b + 1):
        if 0 <= i < len(pages):
            md = (pages[i].get("markdown") or "").strip()
            if md:
                parts.append(f"[page {i + 1}]\n{md}")
    text = "\n\n".join(parts)
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + "\n\n[…truncated]"

    bullets: list[str] = []
    for c in node.children:
        s = ((toc_cache.get(c.anchor) or {}).get("summary") or "").strip()
        if s and not s.startswith("[summary error"):
            bullets.append(f"- {c.title}: {s}")

    out = f"Chapter: {node.title}\n\n"
    if text:
        out += f"Chapter text:\n{text}\n\n"
    if bullets:
        out += "Subsection summaries:\n" + "\n".join(bullets)
    if not text and not bullets:
        out += "[no text available]"
    return out


def _clean_mermaid(val: Any) -> str | None:
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s:
        return None
    # must look like a mermaid diagram with at least one edge/relation
    if not re.search(r"(-->|---|->|==>|\bgraph\b|\bflowchart\b|\bsequenceDiagram\b)", s):
        return None
    return s


def _clean_code(val: Any) -> dict | None:
    if not isinstance(val, dict):
        return None
    body = (val.get("body") or "").strip()
    if not body:
        return None
    return {"cap": (val.get("cap") or "Key structure").strip(), "body": body}


def _norm_module_content(raw: dict) -> dict:
    """Normalize an LLM module reply into the fields the template expects."""
    def s(x: Any) -> str:
        return x.strip() if isinstance(x, str) else ""
    terms = raw.get("terms") or []
    if not isinstance(terms, list):
        terms = []
    terms = [str(t).strip() for t in terms if str(t).strip()][:8]
    return {
        "tagline": s(raw.get("tagline")),
        "expanded": s(raw.get("expanded")),
        "distillation": s(raw.get("distillation")),
        "summary": s(raw.get("summary")),
        "terms": terms,
        "code": _clean_code(raw.get("code")),
        "mermaid": _clean_mermaid(raw.get("mermaid")),
    }


def _is_failed(entry: dict[str, Any] | None) -> bool:
    if not entry:
        return True
    if entry.get("error"):
        return True
    # a usable module has at least the expanded or summary body
    return not (entry.get("expanded") or entry.get("summary"))


def generate_module(skel: dict, pages: list[dict[str, Any]], toc_cache: dict,
                    cache: dict, data_dir: Path) -> dict[str, Any]:
    node = skel["_node"]
    system = _load_prompt("lecture_module.system.txt")
    user = _module_input(node, pages, toc_cache, skel["page_start"], skel["page_end"])
    error: str | None = None
    content: dict[str, Any]
    try:
        raw = llm_client.chat_json(system, user)
        llm_client.llm_sleep()
        if not isinstance(raw, dict):
            raise ValueError("model did not return a JSON object")
        content = _norm_module_content(raw)
        if not (content["expanded"] or content["summary"]):
            raise ValueError("model returned no usable lecture body")
    except Exception as exc:
        content = {"tagline": "", "expanded": "", "distillation": "",
                   "summary": "", "terms": [], "code": None, "mermaid": None}
        error = f"{type(exc).__name__}: {exc}"
        print(f"[lecture] error on {skel['anchor']} ({skel['title']}): {exc}",
              file=sys.stderr)

    rec = {k: skel[k] for k in ("n", "anchor", "title", "layer",
                                "page_start", "page_end", "figs")}
    rec.update(content)
    rec["done_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if error:
        rec["error"] = error
    cache["modules"][node.anchor] = rec
    save_cache(data_dir, cache)
    return rec


# ─── hero + stages (LLM, cheap, cached) ─────────────────────────────────────

def _ensure_stages(mods: list[dict], cache: dict, data_dir: Path) -> None:
    stages = cache.get("stages") or {}
    if stages.get("names") and stages.get("assignments"):
        return
    listing = "\n".join(f"{m['n']}: {m['title']}" for m in mods)
    try:
        raw = llm_client.chat_json(_load_prompt("lecture_stages.system.txt"), listing)
        llm_client.llm_sleep()
        names = {str(k): str(v) for k, v in (raw.get("stage_names") or {}).items()}
        assigns = {str(k): int(v) for k, v in (raw.get("assignments") or {}).items()}
        if names and assigns:
            cache["stages"] = {"names": names, "assignments": assigns}
            save_cache(data_dir, cache)
    except Exception as exc:
        print(f"[lecture] stages failed: {exc}", file=sys.stderr)


def _ensure_meta(paper: dict, mods: list[dict], cache: dict, data_dir: Path) -> None:
    meta = cache.get("meta") or {}
    if meta.get("thesis") and meta.get("lede"):
        return
    pages = paper.get("pages", [])
    abstract = "\n\n".join((pages[i].get("markdown") or "").strip()
                           for i in range(min(2, len(pages))))
    abstract = abstract[:_MAX_CHARS]
    title = paper.get("title") or paper.get("paper_id") or "Paper"
    listing = "\n".join(f"- {m['title']}" for m in mods)
    user = f"Paper title: {title}\n\nAbstract / introduction:\n{abstract}\n\nChapters:\n{listing}"
    hero: dict[str, Any] = {}
    try:
        raw = llm_client.chat_json(_load_prompt("lecture_hero.system.txt"), user)
        llm_client.llm_sleep()
        if isinstance(raw, dict):
            hero = raw
    except Exception as exc:
        print(f"[lecture] hero failed: {exc}", file=sys.stderr)

    def s(x: Any, fb: str = "") -> str:
        return x.strip() if isinstance(x, str) and x.strip() else fb

    cache["meta"] = {
        "title": f"{title} — Lecture",
        "brand": title if len(title) <= 48 else title[:46] + "…",
        "brandSub": s(hero.get("lede"), f"{len(mods)} chapters"),
        "thesis": s(hero.get("thesis"), title),
        "lede": s(hero.get("lede")),
        "framing": s(hero.get("framing")),
        "endTitle": s(hero.get("endTitle"), "Putting it together"),
        "endTag": s(hero.get("endTag")),
        "endBody": s(hero.get("endBody")),
    }
    save_cache(data_dir, cache)


# ─── HTML rendering ─────────────────────────────────────────────────────────

def _render_modules(mods: list[dict], cache: dict) -> list[dict]:
    assigns = (cache.get("stages") or {}).get("assignments") or {}
    out: list[dict] = []
    for m in mods:
        rec = cache["modules"].get(m["anchor"])
        if not rec or _is_failed(rec):
            continue  # skip modules not yet generated / errored
        out.append({**rec, "stage": int(assigns.get(m["n"], 1))})
    # wire up "next" links across the included modules
    for i, m in enumerate(out):
        if i + 1 < len(out):
            nxt = out[i + 1]
            m["next"] = {"id": nxt["n"], "label": f"{nxt['n']} {nxt['title']}",
                         "blurb": nxt.get("tagline", "")}
        m.pop("_node", None)
    return out


def _inject(html: str, marker: str, value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False)
    pat = re.compile(r"/\*" + re.escape(marker) + r"\*/[\s\S]*?;")
    repl = f"/*{marker}*/ {payload};"
    new, n = pat.subn(lambda _m: repl, html, count=1)
    if n == 0:
        raise RuntimeError(f"template marker {marker} not found")
    return new


def render_html(paper: dict, toc_roots: list, cache: dict, data_dir: Path) -> Path:
    mods = build_modules(toc_roots, paper.get("pages", []))
    rendered = _render_modules(mods, cache)
    meta = cache.get("meta") or {"title": "Lecture"}
    names = (cache.get("stages") or {}).get("names") or {}

    html = _TEMPLATE.read_text(encoding="utf-8")
    html = _inject(html, "__META__", meta)
    html = _inject(html, "__STAGE_NAMES__", names)
    html = _inject(html, "__MODULES__", rendered)

    out = html_path(data_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".html.tmp")
    tmp.write_text(html, encoding="utf-8")
    os.replace(tmp, out)
    return out


# ─── orchestration ──────────────────────────────────────────────────────────

def status(paper: dict, toc_roots: list, data_dir: Path) -> dict[str, Any]:
    cache = load_cache(data_dir)
    mods = build_modules(toc_roots, paper.get("pages", []))
    total = len(mods)
    done = sum(1 for m in mods if not _is_failed(cache["modules"].get(m["anchor"])))
    failed = sum(1 for m in mods
                 if (cache["modules"].get(m["anchor"]) or {}).get("error"))
    return {"total": total, "done": done, "failed": failed,
            "built": html_path(data_dir).exists()}


def generate_all(paper: dict, toc_roots: list, toc_cache: dict,
                 data_dir: Path) -> Iterator[dict[str, Any]]:
    """Resumable build. Yields one NDJSON record per module, then hero/stages,
    then a final {done:true} record. Re-running skips good modules."""
    cache = load_cache(data_dir)
    pages = paper.get("pages", [])
    mods = build_modules(toc_roots, pages)
    total = len(mods)

    for i, skel in enumerate(mods, start=1):
        existing = cache["modules"].get(skel["anchor"])
        if not _is_failed(existing):
            yield {"phase": "module", "n": skel["n"], "anchor": skel["anchor"],
                   "title": skel["title"], "ok": True, "cached": True,
                   "index": i, "total": total}
            continue
        rec = generate_module(skel, pages, toc_cache, cache, data_dir)
        yield {"phase": "module", "n": skel["n"], "anchor": skel["anchor"],
               "title": skel["title"], "ok": not rec.get("error"),
               "error": rec.get("error"), "cached": False,
               "index": i, "total": total}

    yield {"phase": "stages", "msg": "grouping chapters"}
    _ensure_stages(mods, cache, data_dir)
    yield {"phase": "hero", "msg": "writing intro"}
    _ensure_meta(paper, mods, cache, data_dir)

    out = render_html(paper, toc_roots, cache, data_dir)
    st = status(paper, toc_roots, data_dir)
    yield {"phase": "done", "done": True, "built": True, "path": str(out), **st}
