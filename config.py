"""Load env.txt and expose typed constants."""
import logging
import os
import sys
import warnings
from pathlib import Path

# Keep the agent trace / server logs clean: the in-process Microsoft GraphRAG backend
# otherwise emits tqdm progress bars, a numpy swapaxes FutureWarning, LiteLLM warnings,
# and asyncio "Task was destroyed" chatter (each GraphRAG search runs in its own
# short-lived event loop in a worker thread, orphaning LiteLLM's background logger).
os.environ.setdefault("TQDM_DISABLE", "1")
warnings.filterwarnings("ignore", message=".*swapaxes.*", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*was never awaited.*", category=RuntimeWarning)
logging.getLogger("LiteLLM").setLevel(logging.ERROR)
logging.getLogger("asyncio").setLevel(logging.CRITICAL)

if getattr(sys, "frozen", False):
    _BASE = Path(sys.executable).parent
else:
    _BASE = Path(__file__).parent

_ENV_PATH = _BASE / "env.txt"


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env(_ENV_PATH)

OPENAI_API_KEY: str   = os.getenv("OPENAI_API_KEY", "")
LLM_MODEL: str        = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_BASE_URL: str     = os.getenv("LLM_BASE_URL", "")
VISION_MODEL: str     = os.getenv("VISION_MODEL", "gpt-4o")
VISION_BASE_URL: str  = os.getenv("VISION_BASE_URL", "")
EMBEDDING_MODEL: str  = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL", "")

# How to turn PDF pages into markdown: "text" (pymupdf4llm) or "vision" (vision LLM).
PDF_EXTRACT_MODE: str = os.getenv("PDF_EXTRACT_MODE", "text").strip().lower()

BASE_DIR: Path = _BASE
DATA_DIR: Path = _BASE / "data"

# --- Agentic GraphRAG (whole-paper chat) -----------------------------------
# Bundled-but-editable template (settings.yaml + GraphRAG prompts) copied into each
# paper's per-paper root at data/<paper_id>/graphrag/ on first build.
GRAPHRAG_TEMPLATE_DIR: Path = _BASE / "graphrag_template"

# Orchestrator knobs (mirror gemini_rag/config.py defaults).
MAX_ITERS: int     = int(os.getenv("MAX_ITERS", "3"))          # search↻sufficiency loop cap
MAX_SNIPPETS: int  = int(os.getenv("MAX_SNIPPETS", "24"))      # hard cap on accumulated evidence
FANOUT_TOP_K: int  = int(os.getenv("FANOUT_TOP_K", "5"))       # snippets per query (reserved)

# Models GraphRAG itself uses for indexing + search (default to the app's models).
GRAPHRAG_CHAT_MODEL: str  = os.getenv("GRAPHRAG_CHAT_MODEL", LLM_MODEL)
GRAPHRAG_EMBED_MODEL: str = os.getenv("GRAPHRAG_EMBED_MODEL", EMBEDDING_MODEL)
