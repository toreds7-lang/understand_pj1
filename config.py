"""Load env.txt and expose typed constants."""
import os
import sys
from pathlib import Path

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

DATA_DIR: Path = _BASE / "data"
