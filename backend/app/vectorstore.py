"""ChromaDB access: connection, jurisdiction listing, and semantic search."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import chromadb

from .azure_client import embed_query
from .config import get_settings

logger = logging.getLogger("vectorstore")


@lru_cache
def get_client():
    """Cached ChromaDB client (HTTP server, or local persistent fallback)."""
    s = get_settings()
    if s.chroma_host:
        return chromadb.HttpClient(host=s.chroma_host, port=s.chroma_port)
    return chromadb.PersistentClient(path=s.chroma_persist_dir)


def get_collection():
    """
    Resolve the collection by name on every call.

    Do NOT cache the collection handle: an ingestion job run with reset=True
    deletes and recreates the collection (new internal UUID), which would make
    a cached handle point at a deleted collection ("does not exist"). The
    client itself is cached; get_or_create_collection is a cheap lookup.
    """
    s = get_settings()
    return get_client().get_or_create_collection(
        name=s.chroma_collection, metadata={"hnsw:space": "cosine"}
    )


def list_jurisdictions() -> list[dict]:
    """Return available jurisdictions with document/chunk counts.

    Merges two sources:
    1. The ``docs_dir`` folder (filesystem) — guarantees every top-level
       jurisdiction folder appears in the list even before ingestion.
    2. ChromaDB metadata — adds chunk/document counts for indexed entries.
    """
    s = get_settings()

    # -- 1. Filesystem scan (source of truth for jurisdictions + doc counts) ---
    fs_data: dict[str, int] = {}  # jurisdiction -> pdf file count on disk
    docs_path = Path(s.docs_dir)
    if docs_path.exists() and docs_path.is_dir():
        for item in docs_path.iterdir():
            if item.is_dir():
                pdf_count = sum(1 for _ in item.rglob("*.pdf"))
                fs_data[item.name] = pdf_count

    # -- 2. ChromaDB counts (populated only after ingestion) -------------------
    chroma_counts: dict[str, int] = {}  # jurisdiction -> indexed chunk count
    try:
        col = get_collection()
        got = col.get(include=["metadatas"])
        metas = got.get("metadatas") or []
        for m in metas:
            j = m.get("jurisdiction", "UNKNOWN")
            if j != "UNKNOWN":
                chroma_counts[j] = chroma_counts.get(j, 0) + 1
    except Exception:  # noqa: BLE001
        logger.warning("Could not query ChromaDB for jurisdiction counts; returning filesystem list only")

    # -- 3. Merge: filesystem jurisdictions + any ChromaDB-only entries --------
    all_jurisdictions = set(fs_data) | {j for j in chroma_counts}
    result = []
    for j in all_jurisdictions:
        result.append({
            "jurisdiction": j,
            "chunks": chroma_counts.get(j, 0),
            "documents": fs_data.get(j, 0),
        })

    return sorted(result, key=lambda r: r["jurisdiction"])


def list_documents() -> list[dict]:
    """Return all documents: PDFs found on disk merged with ChromaDB index data."""
    s = get_settings()

    # -- 1. Filesystem scan: every PDF under docs_dir -------------------------
    fs_docs: dict[str, dict] = {}  # key: relative source_path
    docs_path = Path(s.docs_dir)
    if docs_path.exists() and docs_path.is_dir():
        for pdf in docs_path.rglob("*.pdf"):
            rel = pdf.relative_to(docs_path)
            source_path = str(rel).replace("\\", "/")
            # jurisdiction = top-level folder (mirrors chunker logic)
            parts = rel.parts
            jurisdiction = parts[0] if len(parts) > 1 else "INTERNATIONAL"
            fs_docs[source_path] = {
                "file_name": pdf.name,
                "source_path": source_path,
                "jurisdiction": jurisdiction,
                "pages": 0,
                "chunks": 0,
                "indexed": False,
            }

    # -- 2. ChromaDB: indexed chunk/page counts --------------------------------
    try:
        col = get_collection()
        got = col.get(include=["metadatas"])
        metas = got.get("metadatas") or []
        for m in metas:
            key = m.get("source_path") or m.get("file_name", "UNKNOWN")
            if key in fs_docs:
                fs_docs[key]["chunks"] += 1
                fs_docs[key]["indexed"] = True
            else:
                # doc in ChromaDB but not on disk (e.g. moved/deleted)
                fs_docs[key] = {
                    "file_name": m.get("file_name"),
                    "source_path": key,
                    "jurisdiction": m.get("jurisdiction"),
                    "pages": 0,
                    "chunks": 0,
                    "indexed": True,
                }
                fs_docs[key]["chunks"] += 1

        # second pass for unique page counts
        page_sets: dict[str, set] = {}
        for m in metas:
            key = m.get("source_path") or m.get("file_name", "UNKNOWN")
            if m.get("page_number") is not None:
                page_sets.setdefault(key, set()).add(m["page_number"])
        for key, pages in page_sets.items():
            if key in fs_docs:
                fs_docs[key]["pages"] = len(pages)
    except Exception:  # noqa: BLE001
        logger.warning("Could not query ChromaDB for document index data; showing filesystem only")

    result = list(fs_docs.values())
    return sorted(result, key=lambda r: (r.get("jurisdiction") or "", r.get("file_name") or ""))


def search(query: str, jurisdiction: str | None = None, k: int | None = None) -> list[dict]:
    """Semantic search; optionally filter to a single jurisdiction."""
    s = get_settings()
    k = k or s.retrieval_top_k
    col = get_collection()
    vector = embed_query(query)

    where = None
    if jurisdiction and jurisdiction.upper() not in ("ALL", "ANY", ""):
        where = {"jurisdiction": jurisdiction}

    res = col.query(
        query_embeddings=[vector],
        n_results=k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]

    passages: list[dict] = []
    for doc, meta, dist in zip(docs, metas, dists):
        passages.append(
            {
                "text": doc,
                "jurisdiction": meta.get("jurisdiction"),
                "file_name": meta.get("file_name"),
                "source_path": meta.get("source_path"),
                "page_number": meta.get("page_number"),
                "score": round(1.0 - float(dist), 4),  # cosine distance -> similarity
            }
        )
    return passages
