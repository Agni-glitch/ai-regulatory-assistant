"""FastAPI application entrypoint for the ComplyNexus backend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router
from .config import get_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("main")

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Queue initial indexing when the vector store has not yet been built."""
    if not settings.azure_configured:
        logger.warning(
            "Skipping startup indexing: Azure OpenAI credentials are not configured."
        )
    else:
        try:
            from .tasks import run_ingestion

            task = run_ingestion.delay(only_if_empty=True)
            logger.info("Queued startup check for embeddings (task_id=%s)", task.id)
        except Exception:
            logger.exception("Could not queue startup embedding check")
    yield


app = FastAPI(
    title="ComplyNexus API",
    description="Agentic RAG over country-specific AI regulatory documents.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# All routes are served under /api (nginx proxies /api/* here).
app.include_router(router, prefix="/api")


@app.get("/")
def root() -> dict:
    return {"service": "complynexus-backend", "docs": "/docs"}
