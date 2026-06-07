"""LangChain-based chat and embeddings client with OpenAI backend. Supports vLLM via LLM_BASE_URL."""
from typing import Iterable
import functools

import numpy as np
from langchain.chat_models import init_chat_model
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_openai import OpenAIEmbeddings

from config import (
    OPENAI_API_KEY,
    LLM_MODEL,
    LLM_BASE_URL,
    VISION_MODEL,
    VISION_BASE_URL,
    EMBEDDING_MODEL,
    EMBEDDING_BASE_URL,
)


@functools.lru_cache(maxsize=8)
def _get_llm(model: str):
    """Get or create a cached LangChain LLM instance."""
    kwargs = {}
    if LLM_BASE_URL:
        kwargs["base_url"] = LLM_BASE_URL
        kwargs["api_key"] = "EMPTY"
    else:
        kwargs["api_key"] = OPENAI_API_KEY
    return init_chat_model(model, model_provider="openai", **kwargs)


@functools.lru_cache(maxsize=8)
def _get_vision_llm(model: str):
    """Get or create a cached LangChain vision LLM instance."""
    kwargs = {}
    if VISION_BASE_URL:
        kwargs["base_url"] = VISION_BASE_URL
        kwargs["api_key"] = "EMPTY"
    else:
        kwargs["api_key"] = OPENAI_API_KEY
    return init_chat_model(model, model_provider="openai", **kwargs)


@functools.lru_cache(maxsize=2)
def _get_embeddings(model: str):
    """Get or create a cached LangChain embeddings instance."""
    kwargs = {"model": model}
    if EMBEDDING_BASE_URL:
        kwargs["base_url"] = EMBEDDING_BASE_URL
        kwargs["openai_api_key"] = "EMPTY"
    else:
        kwargs["openai_api_key"] = OPENAI_API_KEY
    return OpenAIEmbeddings(**kwargs)


def _to_lc_messages(messages: list[dict]):
    """Convert OpenAI-format message dicts to LangChain message objects."""
    mapping = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}
    return [mapping[m["role"]](content=m["content"]) for m in messages]


def chat(system: str, user: str, model: str = LLM_MODEL) -> str:
    """Non-streaming chat completion."""
    llm = _get_llm(model)
    result = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    return result.content or ""


def stream_messages(messages: list[dict], model: str = LLM_MODEL):
    """Yield content tokens from a streaming chat completion."""
    llm = _get_llm(model)
    for chunk in llm.stream(_to_lc_messages(messages)):
        token = chunk.content or ""
        if token:
            yield token


def vision_chat(
    system: str,
    user_text: str,
    image_b64: str,
    mime: str = "image/png",
    model: str = VISION_MODEL,
) -> str:
    """Non-streaming multimodal completion: one image plus a text instruction.
    Uses the dedicated vision model/endpoint (VISION_MODEL / VISION_BASE_URL)."""
    llm = _get_vision_llm(model)
    human = HumanMessage(content=[
        {"type": "text", "text": user_text},
        {"type": "image_url",
         "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
    ])
    result = llm.invoke([SystemMessage(content=system), human])
    return result.content or ""


def stream_vision_messages(messages: list[dict], model: str = VISION_MODEL):
    """Multimodal variant: messages may contain content lists with
    {'type': 'image_url', 'image_url': {...}} entries. Uses the dedicated
    vision model/endpoint configured via VISION_MODEL and VISION_BASE_URL."""
    llm = _get_vision_llm(model)
    for chunk in llm.stream(_to_lc_messages(messages)):
        token = chunk.content or ""
        if token:
            yield token


def embed(texts: Iterable[str], model: str = EMBEDDING_MODEL) -> np.ndarray:
    """Embed a list of strings; returns (N, D) float32 array, L2-normalized."""
    texts = list(texts)
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    embedder = _get_embeddings(model)
    vecs = np.array(embedder.embed_documents(texts), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms
