"""Celery application for asynchronous jobs (corpus ingestion)."""

from __future__ import annotations

from celery import Celery

from .config import get_settings

settings = get_settings()

celery_app = Celery(
    "complynexus",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Ingestion is long-running and CPU/IO heavy; one task at a time per worker
    # keeps memory predictable and avoids hammering the embeddings endpoint.
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    result_expires=86400,  # keep job results for a day
)
