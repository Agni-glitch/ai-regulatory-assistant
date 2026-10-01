"""
Celery tasks.

`run_ingestion` runs the full offline pipeline as a background job:
    parse -> clean -> chunk -> index (embed via Azure ada-002 -> ChromaDB)

It reuses the existing CLI stage modules in `ingestion/` (parser.py,
cleaner.py, chunker.py, index.py) by executing them in-process, reporting
progress to the Celery result backend after each stage so the API/UI can poll.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

from .celery_app import celery_app
from .config import get_settings
from .vectorstore import get_collection

# Pipeline stages: (module filename, argv builder given dirs).
STAGES = [
    ("parse", "parser.py", lambda d: [
        "--docs", d["docs"], "--out", str(Path(d["data"]) / "parsed")
    ]),
    ("clean", "cleaner.py", lambda d: [
        "--in", str(Path(d["data"]) / "parsed"),
        "--out", str(Path(d["data"]) / "processed"),
    ]),
    ("chunk", "chunker.py", lambda d: [
        "--in", str(Path(d["data"]) / "processed"),
        "--out", str(Path(d["data"]) / "chunks"),
    ]),
    ("index", "index.py", lambda d: [
        "--chunks", str(Path(d["data"]) / "chunks" / "chunks.jsonl")
    ]
        + (["--reset"] if d["reset"] else [])),
]


def _run_module(ingestion_dir: Path, module: str, argv: list[str]) -> None:
    """Execute an ingestion stage module in-process as if run from the CLI."""
    module_path = ingestion_dir / module
    if not module_path.exists():
        raise FileNotFoundError(f"Ingestion module not found: {module_path}")
    old_argv = sys.argv
    # Ensure intra-package imports (index.py -> `from embedder import ...`) resolve.
    if str(ingestion_dir) not in sys.path:
        sys.path.insert(0, str(ingestion_dir))
    sys.argv = [module, *argv]
    try:
        runpy.run_path(str(module_path), run_name="__main__")
    finally:
        sys.argv = old_argv


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


@celery_app.task(bind=True, name="app.tasks.run_ingestion")
def run_ingestion(
    self,
    reset: bool = False,
    skip_index: bool = False,
    only_if_empty: bool = False,
) -> dict:
    """
    Run the ingestion pipeline. Reports PROGRESS state per stage.

    Args:
        reset: drop and rebuild the ChromaDB collection before indexing.
        skip_index: run offline stages only (no Azure / ChromaDB).
        only_if_empty: skip the job when the target collection already has vectors.
    """
    settings = get_settings()
    if only_if_empty:
        try:
            existing_count = get_collection().count()
        except Exception as exc:
            raise self.retry(exc=exc, countdown=10, max_retries=6)
        if existing_count:
            return {
                "stage": "done",
                "skip_index": False,
                "reset": False,
                "indexed": False,
                "message": f"Collection already contains {existing_count} embeddings.",
            }

    ingestion_dir = Path(settings.ingestion_dir).resolve()
    cwd = Path.cwd()
    data_roots = (cwd / "data" / "ingest", cwd / "data")
    data_dir = next(
        (
            root
            for root in data_roots
            if (root / "chunks" / "chunks.jsonl").is_file()
            or any((root / name).is_dir() for name in ("parsed", "processed"))
        ),
        cwd / "data",
    )
    docs_dir = Path(settings.docs_dir).resolve()
    has_pdfs = docs_dir.is_dir() and any(
        path.is_file() and path.suffix.lower() == ".pdf"
        for path in docs_dir.rglob("*")
    )
    parsed_dir = data_dir / "parsed"
    processed_dir = data_dir / "processed"
    chunks_file = data_dir / "chunks" / "chunks.jsonl"
    has_parsed = parsed_dir.is_dir() and any(
        path.is_file() and path.suffix.lower() == ".json"
        and not path.name.startswith("_")
        for path in parsed_dir.rglob("*")
    )
    has_processed = processed_dir.is_dir() and any(
        path.is_file() and path.suffix.lower() == ".json"
        and not path.name.startswith("_")
        for path in processed_dir.rglob("*")
    )

    if has_pdfs:
        stages = STAGES
    elif chunks_file.is_file():
        stages = STAGES[3:]
    elif has_parsed:
        stages = STAGES[1:]
    elif has_processed:
        stages = STAGES[2:]
    else:
        raise FileNotFoundError(
            f"No PDFs found under {docs_dir}, and no parsed/processed data or "
            f"chunks found under {data_dir}."
        )
    if skip_index:
        stages = stages[:-1]

    dirs = {
        "docs": str(docs_dir),
        "reset": reset,
        "data": str(data_dir),
    }
    total = len(stages)

    for i, (name, module, build_argv) in enumerate(stages, start=1):
        self.update_state(
            state="PROGRESS",
            meta={"stage": name, "step": i, "total": total,
                  "message": f"Running {name} ({i}/{total})"},
        )
        _run_module(ingestion_dir, module, build_argv(dirs))

    # Summarize from the artifacts the stages wrote or reused.
    parse_summary = _read_json(cwd / "data/parsed/_parse_summary.json")
    chunk_summary = _read_json(cwd / "data/chunks/_chunk_summary.json")

    return {
        "stage": "done",
        "skip_index": skip_index,
        "reset": reset,
        "documents_parsed": parse_summary.get("parsed"),
        "documents_failed": parse_summary.get("failed"),
        "total_chunks": chunk_summary.get("total_chunks"),
        "per_jurisdiction": chunk_summary.get("per_jurisdiction"),
        "indexed": not skip_index,
    }


@celery_app.task(bind=True, name="app.tasks.reprocess_document")
def reprocess_document(self, source_path: str) -> dict:
    """
    Reprocess a single document: parse -> clean -> chunk -> delete old chunks -> embed -> upsert.

    Imports parse_pdf / clean_document / chunk_document directly from the
    ingestion modules (available in the worker container at ingestion_dir).
    """
    settings = get_settings()
    ingestion_dir = Path(settings.ingestion_dir).resolve()
    docs_dir = Path(settings.docs_dir).resolve()

    # Make ingestion helpers importable.
    if str(ingestion_dir) not in sys.path:
        sys.path.insert(0, str(ingestion_dir))

    pdf_path = docs_dir / source_path
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # --- Stage 1: Parse -------------------------------------------------------
    self.update_state(state="PROGRESS",
                      meta={"stage": "parse", "step": 1, "total": 3,
                            "message": "Parsing PDF"})
    import parser as _parser  # noqa: PLC0415
    parsed = _parser.parse_pdf(pdf_path, docs_dir)

    # --- Stage 2: Clean -------------------------------------------------------
    self.update_state(state="PROGRESS",
                      meta={"stage": "clean", "step": 2, "total": 3,
                            "message": "Cleaning text"})
    import cleaner as _cleaner  # noqa: PLC0415
    cleaned = _cleaner.clean_document(parsed)

    # --- Stage 3: Chunk + delete old + embed + upsert -------------------------
    self.update_state(state="PROGRESS",
                      meta={"stage": "index", "step": 3, "total": 3,
                            "message": "Indexing into ChromaDB"})
    import chunker as _chunker  # noqa: PLC0415
    chunks = _chunker.chunk_document(cleaned)

    if not chunks:
        return {"source_path": source_path, "chunks": 0,
                "message": "No text extracted — document may be scanned/image-only."}

    # Connect to ChromaDB.
    import chromadb as _chroma  # noqa: PLC0415
    if settings.chroma_host:
        client = _chroma.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
    else:
        client = _chroma.PersistentClient(path=settings.chroma_persist_dir)
    collection = client.get_or_create_collection(
        name=settings.chroma_collection, metadata={"hnsw:space": "cosine"}
    )

    # Delete existing chunks for this document so a reprocess is a clean replace.
    existing = collection.get(where={"source_path": source_path}, include=[])
    if existing.get("ids"):
        collection.delete(ids=existing["ids"])

    # Embed and upsert in batches.
    from embedder import Embedder  # noqa: PLC0415
    embedder = Embedder()

    texts = [c["text"] for c in chunks]
    ids = [c["id"] for c in chunks]
    metas = [{k: v for k, v in c.items() if k not in ("text", "id")} for c in chunks]

    batch_size = 128
    for start in range(0, len(chunks), batch_size):
        batch_texts = texts[start:start + batch_size]
        batch_ids = ids[start:start + batch_size]
        batch_metas = metas[start:start + batch_size]
        vectors = embedder.embed(batch_texts)
        collection.upsert(
            ids=batch_ids,
            embeddings=vectors,
            documents=batch_texts,
            metadatas=batch_metas,
        )

    return {"source_path": source_path, "chunks": len(chunks), "indexed": True}
