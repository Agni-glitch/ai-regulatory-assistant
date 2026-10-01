"""
Data Ingestion — Embeddings (Azure OpenAI text-embedding-ada-002).

Thin, reusable wrapper around the Azure OpenAI embeddings endpoint with
batching and simple retry/backoff. Used by index.py during ingestion.

Configuration comes from environment variables (see .env.example):
    AZURE_OPENAI_ENDPOINT
    AZURE_OPENAI_API_KEY
    AZURE_OPENAI_API_VERSION
    AZURE_OPENAI_EMBEDDING_DEPLOYMENT

Requires: openai>=1.0
"""

from __future__ import annotations

import logging
import os
import time

from openai import AzureOpenAI

logger = logging.getLogger("embedder")

# ada-002 accepts large batches; keep it modest to stay under request limits.
BATCH_SIZE = 64
MAX_RETRIES = 5


def build_client() -> AzureOpenAI:
    """Construct an AzureOpenAI client from environment variables."""
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
    if not endpoint or not api_key:
        raise RuntimeError(
            "AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY must be set."
        )
    return AzureOpenAI(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
    )


class Embedder:
    """Batched embedding with retry. Reusable across ingestion runs."""

    def __init__(self, client: AzureOpenAI | None = None, deployment: str | None = None):
        self.client = client or build_client()
        self.deployment = deployment or os.environ.get(
            "AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002"
        )

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        last_exc: Exception | None = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.client.embeddings.create(
                    model=self.deployment, input=texts
                )
                # Preserve input order.
                return [item.embedding for item in sorted(resp.data, key=lambda d: d.index)]
            except Exception as exc:  # noqa: BLE001 - retry on any transient error
                last_exc = exc
                wait = min(2 ** attempt, 30)
                logger.warning(
                    "Embedding batch failed (attempt %d/%d): %s — retrying in %ds",
                    attempt, MAX_RETRIES, exc, wait,
                )
                time.sleep(wait)
        raise RuntimeError(f"Embedding failed after {MAX_RETRIES} attempts") from last_exc

    def embed(self, texts: list[str], batch_size: int = BATCH_SIZE) -> list[list[float]]:
        """Embed a list of texts, returning vectors in the same order."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            vectors.extend(self._embed_batch(batch))
            logger.info("Embedded %d/%d", min(start + batch_size, len(texts)), len(texts))
        return vectors
