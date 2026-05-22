"""OpenAI client + embeddings. Supports vLLM via LLM_BASE_URL."""
from typing import Iterable

import numpy as np
from openai import OpenAI

from config import (
    OPENAI_API_KEY,
    LLM_MODEL,
    LLM_BASE_URL,
    EMBEDDING_MODEL,
    EMBEDDING_BASE_URL,
)


def _make_client() -> OpenAI:
    if LLM_BASE_URL:
        return OpenAI(api_key="EMPTY", base_url=LLM_BASE_URL)
    return OpenAI(api_key=OPENAI_API_KEY)


def _make_embedding_client() -> OpenAI:
    if EMBEDDING_BASE_URL:
        return OpenAI(api_key="EMPTY", base_url=EMBEDDING_BASE_URL)
    return OpenAI(api_key=OPENAI_API_KEY)


_client: OpenAI = _make_client()
_embed_client: OpenAI = _make_embedding_client()


def chat(system: str, user: str, model: str = LLM_MODEL) -> str:
    resp = _client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""


def stream_messages(messages: list[dict], model: str = LLM_MODEL):
    """Yield content tokens from an OpenAI streaming chat completion."""
    with _client.chat.completions.create(
        model=model, messages=messages, stream=True
    ) as stream:
        for event in stream:
            token = event.choices[0].delta.content or ""
            if token:
                yield token


def stream_vision_messages(messages: list[dict], model: str = LLM_MODEL):
    """Multimodal variant: messages may contain content lists with
    {'type': 'image_url', 'image_url': {...}} entries. The OpenAI SDK accepts
    this shape directly; this helper exists to make intent explicit at call sites."""
    yield from stream_messages(messages, model=model)


def embed(texts: Iterable[str], model: str = EMBEDDING_MODEL) -> np.ndarray:
    """Embed a list of strings; returns (N, D) float32 array, L2-normalized."""
    texts = list(texts)
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    resp = _embed_client.embeddings.create(model=model, input=texts)
    vecs = np.array([d.embedding for d in resp.data], dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms
