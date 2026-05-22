# Prompts

Editable prompt templates loaded at runtime by `ai.py`. Edit any file and save — the next request picks up the change (no server restart needed).

| File | Used by | Placeholders |
| --- | --- | --- |
| `define.system.txt` | Ctrl+D (define word) | — |
| `define.user.txt` | Ctrl+D | `{word}`, `{before}`, `{after}` |
| `explain.system.txt` | Ctrl+E (explain sentence) | — |
| `explain.user.txt` | Ctrl+E | `{sentence}`, `{before}`, `{after}` |
| `chat.system.txt` | RAG chat | — |
| `chat.user.txt` | RAG chat | `{scope_note}`, `{context}`, `{question}` |

Keep the placeholder names — `ai.py` fills them with `str.format`. Any literal `{` or `}` in your text must be doubled (`{{`, `}}`).
