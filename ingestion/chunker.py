"""
Data Ingestion — Chunking.

Reads cleaned JSON from data/processed/ (output of cleaner.py) and produces
retrieval-ready chunks in data/chunks/chunks.jsonl (one JSON object per line).

Chunking is page-aware: chunks never span pages, so every chunk maps to an
exact (file, page) citation. Within a page we use a sliding character window
with overlap, breaking on whitespace so words are not split.

Each chunk carries metadata used for filtering and citation:
  - jurisdiction: top-level folder under document/ (e.g. "US", "ASEAN COUNTRIES")
                  or "INTERNATIONAL" for standards sitting at the corpus root
                  (ISO/IEC, OECD, World Bank, ...).
  - file_name, source_path, page_number, chunk_index.

Pure Python, standard library only.

Run from the repo root:
    python ingestion/chunker.py
    python ingestion/chunker.py --in data/processed --out data/chunks
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("chunker")

# Sliding-window sizes in characters. ada-002 handles up to ~8k tokens; we keep
# chunks small so retrieved context is focused and citations are precise.
CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200
# Skip chunks shorter than this (page headers, stray fragments).
MIN_CHUNK_CHARS = 80

INTERNATIONAL = "INTERNATIONAL"


def jurisdiction_for(relative_dir: str) -> str:
    """First path component is the jurisdiction; root-level files are standards."""
    relative_dir = (relative_dir or "").strip().strip("/")
    if not relative_dir:
        return INTERNATIONAL
    return relative_dir.split("/")[0]


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    """Yield overlapping windows of `text`, breaking on whitespace where possible."""
    text = text.strip()
    if not text:
        return
    if len(text) <= size:
        yield text
        return

    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        # Try to break on the last whitespace within the window (avoid mid-word).
        if end < n:
            window = text[start:end]
            break_at = window.rfind(" ")
            if break_at > size * 0.5:  # only if it doesn't shrink the chunk too much
                end = start + break_at
        chunk = text[start:end].strip()
        if chunk:
            yield chunk
        if end >= n:
            break
        start = max(end - overlap, start + 1)


def slug(source_path: str) -> str:
    """Stable id-safe slug from a source path."""
    return (
        source_path.lower()
        .replace(" ", "_")
        .replace("/", "__")
        .replace(".json", "")
        .replace(".pdf", "")
    )


def chunk_document(doc: dict) -> list[dict]:
    """Turn one cleaned document into a list of chunk records."""
    source_path = doc.get("source_path", doc.get("file_name", "unknown"))
    file_name = doc.get("file_name", Path(source_path).name)
    jurisdiction = jurisdiction_for(doc.get("relative_dir", ""))
    base = slug(source_path)

    chunks: list[dict] = []
    for page in doc.get("pages", []):
        page_number = page.get("page_number")
        text = page.get("text", "")
        for i, piece in enumerate(chunk_text(text)):
            if len(piece) < MIN_CHUNK_CHARS:
                continue
            chunk_index = len(chunks)
            chunks.append(
                {
                    "id": f"{base}__p{page_number}__c{i}",
                    "text": piece,
                    "jurisdiction": jurisdiction,
                    "file_name": file_name,
                    "source_path": source_path,
                    "page_number": page_number,
                    "chunk_index": chunk_index,
                }
            )
    return chunks


def main() -> None:
    ap = argparse.ArgumentParser(description="Chunk cleaned PDF JSON into JSONL.")
    ap.add_argument("--in", dest="in_dir", default="data/processed",
                    help="Input cleaned-JSON root.")
    ap.add_argument("--out", dest="out_dir", default="data/chunks",
                    help="Output directory for chunks.jsonl.")
    args = ap.parse_args()

    in_root = Path(args.in_dir).resolve()
    out_root = Path(args.out_dir).resolve()

    if not in_root.exists():
        logger.error("Input directory does not exist: %s", in_root)
        raise SystemExit(1)

    out_root.mkdir(parents=True, exist_ok=True)

    json_paths = sorted(
        p for p in in_root.rglob("*.json") if not p.name.startswith("_")
    )
    if not json_paths:
        logger.warning("No cleaned JSON found under %s", in_root)
        return

    logger.info("Found %d cleaned JSON files under %s", len(json_paths), in_root)

    out_path = out_root / "chunks.jsonl"
    total_chunks = 0
    per_jurisdiction: dict[str, int] = {}

    with out_path.open("w", encoding="utf-8") as fh:
        for json_path in json_paths:
            try:
                doc = json.loads(json_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.exception("Failed to read %s: %s", json_path, exc)
                continue

            chunks = chunk_document(doc)
            for chunk in chunks:
                fh.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                per_jurisdiction[chunk["jurisdiction"]] = (
                    per_jurisdiction.get(chunk["jurisdiction"], 0) + 1
                )
            total_chunks += len(chunks)
            logger.info("Chunked %-55s -> %d chunks", doc.get("source_path"), len(chunks))

    summary_path = out_root / "_chunk_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "total_chunks": total_chunks,
                "documents": len(json_paths),
                "per_jurisdiction": dict(sorted(per_jurisdiction.items())),
                "chunk_size": CHUNK_SIZE,
                "chunk_overlap": CHUNK_OVERLAP,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info(
        "Done. %d chunks from %d docs across %d jurisdictions. Output: %s",
        total_chunks,
        len(json_paths),
        len(per_jurisdiction),
        out_path,
    )


if __name__ == "__main__":
    main()
