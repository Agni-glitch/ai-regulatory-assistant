"""
Data Ingestion — full pipeline runner.

Chains the four offline stages end to end:
    1. parser.py   : document/  -> data/parsed/      (PDF -> JSON)
    2. cleaner.py  : data/parsed/ -> data/processed/ (normalize/clean)
    3. chunker.py  : data/processed/ -> data/chunks/ (page-aware chunks)
    4. index.py    : data/chunks/ -> ChromaDB        (embed + upsert)

If no source PDFs are available, existing parsed/processed JSON or chunks are
reused, allowing an existing corpus under data/ingest/ to be indexed directly.
Stages 1-3 are pure Python (no network). Stage 4 needs Azure OpenAI creds and
a reachable ChromaDB (see .env.example).

Run from the repo root:
    python ingestion/run_pipeline.py
    python ingestion/run_pipeline.py --docs path/to/pdfs --skip-index
"""

from __future__ import annotations

import argparse
import logging
import runpy
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("pipeline")

HERE = Path(__file__).parent


def _has_files(root: Path, suffix: str) -> bool:
    """Return whether root contains a non-summary file with the given suffix."""
    return root.is_dir() and any(
        path.is_file()
        and path.suffix.lower() == suffix
        and not path.name.startswith("_")
        for path in root.rglob("*")
    )


def _has_pdfs(root: Path) -> bool:
    return root.is_dir() and any(
        path.is_file() and path.suffix.lower() == ".pdf"
        for path in root.rglob("*")
    )


def _default_data_root() -> Path:
    """Prefer the host's data/ingest corpus, then the standard data directory."""
    candidates = (Path("data/ingest"), Path("data"))
    for candidate in candidates:
        if (
            _has_pdfs(candidate)
            or _has_files(candidate / "parsed", ".json")
            or _has_files(candidate / "processed", ".json")
            or (candidate / "chunks" / "chunks.jsonl").is_file()
        ):
            return candidate.resolve()
    return Path("data").resolve()


def _resolve_docs_root(docs_root: Path, data_root: Path) -> Path | None:
    """Find PDFs at the requested location or in the selected data directory."""
    for candidate in dict.fromkeys((docs_root, data_root)):
        if _has_pdfs(candidate):
            return candidate
    return None


def _offline_stages(data_root: Path, has_pdfs: bool) -> list[tuple[str, list[str]]]:
    """Choose only the offline stages needed for the available source data."""
    parsed = data_root / "parsed"
    processed = data_root / "processed"
    chunks = data_root / "chunks" / "chunks.jsonl"

    if has_pdfs:
        return [
            ("parser.py", []),
            ("cleaner.py", ["--in", str(parsed), "--out", str(processed)]),
            ("chunker.py", ["--in", str(processed), "--out", str(data_root / "chunks")]),
        ]
    if chunks.is_file():
        return []
    if _has_files(parsed, ".json"):
        return [
            ("cleaner.py", ["--in", str(parsed), "--out", str(processed)]),
            ("chunker.py", ["--in", str(processed), "--out", str(data_root / "chunks")]),
        ]
    if _has_files(processed, ".json"):
        return [
            ("chunker.py", ["--in", str(processed), "--out", str(data_root / "chunks")]),
        ]
    raise FileNotFoundError(
        f"No PDFs, parsed/processed JSON, or existing chunks found. "
        f"Provide PDFs with --docs or prepared data under {data_root}."
    )


def run_stage(module: str, argv: list[str]) -> None:
    """Run an ingestion stage module as if invoked from the command line."""
    logger.info("=== Running %s %s ===", module, " ".join(argv))
    old_argv = sys.argv
    # Ensure intra-package imports (index.py -> `from embedder import ...`) resolve.
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    sys.argv = [module, *argv]
    try:
        runpy.run_path(str(HERE / module), run_name="__main__")
    finally:
        sys.argv = old_argv


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the full ingestion pipeline.")
    ap.add_argument("--docs", default="document", help="Source PDFs root.")
    ap.add_argument(
        "--data-dir",
        help="Root for parsed/, processed/, and chunks/ (auto-detected by default).",
    )
    ap.add_argument("--skip-index", action="store_true",
                    help="Run parse/clean/chunk only (no Azure / ChromaDB).")
    ap.add_argument("--reset", action="store_true",
                    help="Reset the ChromaDB collection before indexing.")
    args = ap.parse_args()

    data_root = (
        Path(args.data_dir).resolve() if args.data_dir else _default_data_root()
    )
    docs_root = _resolve_docs_root(Path(args.docs).resolve(), data_root)
    offline_stages = _offline_stages(data_root, docs_root is not None)

    if docs_root is None:
        logger.info("No source PDFs found; reusing prepared data under %s", data_root)
    for module, argv in offline_stages:
        if module == "parser.py":
            argv = ["--docs", str(docs_root), "--out", str(data_root / "parsed")]
        run_stage(module, argv)

    if args.skip_index:
        logger.info("Skipping index stage (--skip-index). Offline stages complete.")
        return

    index_argv = ["--chunks", str(data_root / "chunks" / "chunks.jsonl")]
    if args.reset:
        index_argv.append("--reset")
    run_stage("index.py", index_argv)
    logger.info("Pipeline complete.")


if __name__ == "__main__":
    main()
