"""
Data Ingestion — Indexing into ChromaDB.

Reads chunks from data/chunks/chunks.jsonl (output of chunker.py), embeds them
with Azure OpenAI ada-002 (embedder.py), and upserts them into a ChromaDB
collection with metadata (jurisdiction, file, page) for filtered retrieval.

Works against a ChromaDB server (HttpClient) when CHROMA_HOST is set, or a
local persistent client otherwise (data/chroma/).

Requires: chromadb>=0.5, openai>=1.0

Run from the repo root (with .env exported, e.g. `set -a; . ./.env; set +a`):
    python ingestion/index.py
    python ingestion/index.py --chunks data/chunks/chunks.jsonl --batch 128
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

import chromadb

from embedder import Embedder

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("index")

UPSERT_BATCH = 128


def get_chroma_client():
    """HTTP client if CHROMA_HOST is set, else a local persistent client."""
    host = os.environ.get("CHROMA_HOST")
    if host:
        port = int(os.environ.get("CHROMA_PORT", "8000"))
        logger.info("Connecting to ChromaDB server at %s:%s", host, port)
        return chromadb.HttpClient(host=host, port=port)
    persist_dir = os.environ.get("CHROMA_PERSIST_DIR", "data/chroma")
    logger.info("Using local persistent ChromaDB at %s", persist_dir)
    Path(persist_dir).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=persist_dir)


def load_chunks(path: Path) -> list[dict]:
    chunks: list[dict] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                chunks.append(json.loads(line))
    return chunks


def main() -> None:
    ap = argparse.ArgumentParser(description="Embed chunks and index into ChromaDB.")
    ap.add_argument("--chunks", default="data/chunks/chunks.jsonl",
                    help="Path to chunks.jsonl.")
    ap.add_argument("--batch", type=int, default=UPSERT_BATCH,
                    help="Upsert batch size.")
    ap.add_argument("--reset", action="store_true",
                    help="Delete and recreate the collection before indexing.")
    args = ap.parse_args()

    chunks_path = Path(args.chunks).resolve()
    if not chunks_path.exists():
        logger.error("Chunks file not found: %s (run chunker.py first)", chunks_path)
        raise SystemExit(1)

    chunks = load_chunks(chunks_path)
    if not chunks:
        logger.warning("No chunks to index.")
        return
    logger.info("Loaded %d chunks from %s", len(chunks), chunks_path)

    client = get_chroma_client()
    collection_name = os.environ.get("CHROMA_COLLECTION", "ai_regulations")

    if args.reset:
        try:
            client.delete_collection(collection_name)
            logger.info("Deleted existing collection %s", collection_name)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=collection_name, metadata={"hnsw:space": "cosine"}
    )

    embedder = Embedder()

    total = len(chunks)
    for start in range(0, total, args.batch):
        batch = chunks[start:start + args.batch]
        texts = [c["text"] for c in batch]
        vectors = embedder.embed(texts)
        collection.upsert(
            ids=[c["id"] for c in batch],
            embeddings=vectors,
            documents=texts,
            metadatas=[
                {
                    "jurisdiction": c["jurisdiction"],
                    "file_name": c["file_name"],
                    "source_path": c["source_path"],
                    "page_number": c["page_number"],
                    "chunk_index": c["chunk_index"],
                }
                for c in batch
            ],
        )
        logger.info("Upserted %d/%d", min(start + args.batch, total), total)

    logger.info(
        "Done. Collection '%s' now reports %d items.",
        collection_name,
        collection.count(),
    )


if __name__ == "__main__":
    main()
