"""PyInstaller runtime hook for the bundled GraphRAG stack.

Runs before the app's own imports. Points tiktoken at the bundled encoding cache so the
frozen, possibly-offline exe never tries to download `o200k_base`/`cl100k_base`, and
silences GraphRAG's tqdm progress bars in the packaged console.
"""
import os
import sys
from pathlib import Path

_base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))

# tiktoken: prefer the bundled cache (sha1-named blobs warmed at build time).
_cache = _base / "tiktoken_cache"
if _cache.exists():
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(_cache))

# Also honor an external cache next to the exe if the user dropped one there.
_ext_cache = Path(sys.executable).parent / "tiktoken_cache"
if _ext_cache.exists():
    os.environ["TIKTOKEN_CACHE_DIR"] = str(_ext_cache)

os.environ.setdefault("TQDM_DISABLE", "1")
