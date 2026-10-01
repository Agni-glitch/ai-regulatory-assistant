"""Azure OpenAI client wrappers for chat completion and embeddings."""

from __future__ import annotations

import logging
from functools import lru_cache

from openai import AzureOpenAI

from .config import get_settings

logger = logging.getLogger("azure_client")


@lru_cache
def get_client() -> AzureOpenAI:
    """Singleton AzureOpenAI client built from settings."""
    s = get_settings()
    if not s.azure_configured:
        raise RuntimeError(
            "Azure OpenAI is not configured. Set AZURE_OPENAI_ENDPOINT and "
            "AZURE_OPENAI_API_KEY."
        )
    return AzureOpenAI(
        azure_endpoint=s.azure_openai_endpoint,
        api_key=s.azure_openai_api_key,
        api_version=s.azure_openai_api_version,
    )


def embed_query(text: str) -> list[float]:
    """Embed a single query string with the ada-002 deployment."""
    s = get_settings()
    resp = get_client().embeddings.create(
        model=s.azure_openai_embedding_deployment, input=[text]
    )
    return resp.data[0].embedding


def chat(messages: list[dict], tools: list[dict] | None = None,
         temperature: float = 0.1):
    """Call the chat deployment. Returns the raw OpenAI response object."""
    s = get_settings()
    kwargs: dict = {
        "model": s.azure_openai_chat_deployment,
        "messages": messages,
        "temperature": temperature,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    return get_client().chat.completions.create(**kwargs)
