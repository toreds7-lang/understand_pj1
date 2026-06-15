# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import (
    collect_submodules, collect_data_files, collect_all, copy_metadata,
)

hiddenimports = [
    'ai', 'config', 'figure_explain', 'highlights',
    'run', 'toc_summary', 'toc', 'rag', 'graph', 'wiki',
    'extract', 'pipeline', 'llm_client',
    # Agentic GraphRAG (whole-paper chat)
    'agentic_rag', 'graphrag_manager',
]
hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('langchain')
hiddenimports += collect_submodules('langchain_openai')
hiddenimports += collect_submodules('langchain_core')
hiddenimports += collect_submodules('graphrag_qa')
hiddenimports += collect_submodules('tiktoken_ext')
hiddenimports += ['tiktoken_ext.openai_public']

datas = []
binaries = []
datas += collect_data_files('pymupdf')
datas += collect_data_files('pymupdf4llm')
datas += collect_data_files('tiktoken')
datas += collect_data_files('tiktoken_ext')

# Pre-warmed tiktoken encoding cache so the frozen exe never downloads o200k_base.
datas += [('tiktoken_cache', 'tiktoken_cache')]

# --- Microsoft GraphRAG stack + native query/index deps --------------------
# collect_all pulls package code (as data, for the many dynamically-imported
# workflow modules), bundled data files, and native binaries in one shot.
_GRAPHRAG_PKGS = [
    'graphrag', 'graphrag_cache', 'graphrag_chunking', 'graphrag_common',
    'graphrag_input', 'graphrag_llm', 'graphrag_storage', 'graphrag_vectors',
    'lancedb', 'pyarrow', 'litellm', 'graspologic_native',
    'spacy', 'thinc', 'blis', 'networkx', 'json_repair', 'fastuuid',
    'nltk', 'textblob', 'magika', 'markitdown',
]
for _pkg in _GRAPHRAG_PKGS:
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# Several of these read their own version at runtime via importlib.metadata; without
# the dist-info that raises PackageNotFoundError. Copy metadata for the offenders.
for _pkg in ('graphrag', 'graphrag-cache', 'graphrag-chunking', 'graphrag-common',
             'graphrag-input', 'graphrag-llm', 'graphrag-storage', 'graphrag-vectors',
             'litellm', 'tiktoken', 'spacy', 'thinc', 'numpy', 'tqdm', 'regex',
             'networkx', 'pandas', 'pyarrow', 'lancedb', 'rich', 'typer'):
    try:
        datas += copy_metadata(_pkg)
    except Exception:
        pass

a = Analysis(
    ['serve.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['rthook_graphrag.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='paper_reader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
