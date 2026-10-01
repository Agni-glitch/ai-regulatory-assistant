"""
Data Ingestion — Parsing and Text Extraction.

Uses pypdf (pure Python), which installs on any Python version including 3.14
with no compiler, no admin rights, and no system dependencies.

Walks docs/, extracts text per page, writes one JSON per PDF into
data/parsed/ (mirroring folder structure), plus a triage summary at
data/parsed/_parse_summary.json.

Pure Python, no OCR. Scanned/image-only PDFs are FLAGGED (likely_scanned),
not OCR'd.

Run from the repo root:
    python ingestion/parser.py
    python ingestion/parser.py --docs docs --out data/parsed
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
from pathlib import Path

from pypdf import PdfReader

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("parser")

# Pages with fewer than this many characters are treated as "no usable text"
# (likely scanned / image-only), since OCR is unavailable in this environment.
MIN_CHARS_PER_PAGE = 20


def parse_pdf(pdf_path: Path, docs_root: Path) -> dict:
    """Parse a single PDF into a structured dict using pypdf."""
    pdf_path = Path(pdf_path)
    docs_root = Path(docs_root)

    relative = pdf_path.relative_to(docs_root)
    relative_dir = str(relative.parent).replace("\\", "/")
    if relative_dir == ".":
        relative_dir = ""

    pages: list[dict] = []
    empty_pages = 0
    low_text_pages = 0
    total_chars = 0

    reader = PdfReader(str(pdf_path))

    # Some PDFs are encrypted with an empty password; try to decrypt so we can
    # still read them. If it fails, the outer try/except in main() records it.
    if reader.is_encrypted:
        try:
            reader.decrypt("")
        except Exception:
            logger.warning("Could not decrypt %s (encrypted).", pdf_path.name)

    num_pages = len(reader.pages)
    for index in range(num_pages):
        page_number = index + 1
        try:
            text = (reader.pages[index].extract_text() or "").strip()
        except Exception as exc:
            # Don't fail the whole file because one page is malformed.
            logger.warning(
                "Text extraction failed on %s page %d: %s",
                pdf_path.name,
                page_number,
                exc,
            )
            text = ""

        char_count = len(text)
        total_chars += char_count

        if char_count == 0:
            empty_pages += 1
        if char_count < MIN_CHARS_PER_PAGE:
            low_text_pages += 1

        pages.append(
            {
                "page_number": page_number,
                "method": "pypdf_text",
                "char_count": char_count,
                "text": text,
            }
        )

    avg_chars = round(total_chars / num_pages) if num_pages else 0
    likely_scanned = num_pages > 0 and low_text_pages >= max(1, num_pages // 2)

    return {
        "source_path": str(relative).replace("\\", "/"),
        "file_name": pdf_path.name,
        "relative_dir": relative_dir,
        "num_pages": num_pages,
        "extraction": {
            "primary_method": "pypdf_text",
            "ocr_used": False,
            "ocr_available": False,
        },
        "quality": {
            "total_chars": total_chars,
            "empty_pages": empty_pages,
            "low_text_pages": low_text_pages,
            "avg_chars_per_page": avg_chars,
            "likely_scanned": likely_scanned,
        },
        "pages": pages,
        "parsed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }


def find_pdfs(docs_root: Path) -> list[Path]:
    """Find PDFs case-insensitively (catches both .pdf and .PDF)."""
    return sorted(p for p in docs_root.rglob("*") if p.suffix.lower() == ".pdf")


def main() -> None:
    arg_parser = argparse.ArgumentParser(description="Parse PDFs into JSON.")
    arg_parser.add_argument("--docs", default="docs", help="Input PDFs root.")
    arg_parser.add_argument("--out", default="data/parsed", help="Output root.")
    args = arg_parser.parse_args()

    docs_root = Path(args.docs).resolve()
    out_root = Path(args.out).resolve()

    if not docs_root.exists():
        logger.error("Docs directory does not exist: %s", docs_root)
        raise SystemExit(1)

    out_root.mkdir(parents=True, exist_ok=True)

    pdf_paths = find_pdfs(docs_root)
    if not pdf_paths:
        logger.warning("No PDFs found under %s", docs_root)
        return

    logger.info("Found %d PDFs under %s", len(pdf_paths), docs_root)

    summary: list[dict] = []
    failures: list[str] = []

    for pdf_path in pdf_paths:
        rel = str(pdf_path.relative_to(docs_root)).replace("\\", "/")
        try:
            result = parse_pdf(pdf_path, docs_root)
        except Exception as exc:
            logger.exception("Failed to parse %s: %s", rel, exc)
            failures.append(rel)
            continue

        out_path = out_root / Path(result["source_path"]).with_suffix(".json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        summary.append(
            {
                "file": result["source_path"],
                "pages": result["num_pages"],
                "total_chars": result["quality"]["total_chars"],
                "likely_scanned": result["quality"]["likely_scanned"],
            }
        )
        logger.info(
            "Parsed %-55s pages=%-4d chars=%-7d scanned=%s",
            result["source_path"],
            result["num_pages"],
            result["quality"]["total_chars"],
            result["quality"]["likely_scanned"],
        )

    summary_path = out_root / "_parse_summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "total": len(pdf_paths),
                "parsed": len(summary),
                "failed": failures,
                "scanned_or_low_text": [
                    s["file"] for s in summary if s["likely_scanned"]
                ],
                "files": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    logger.info(
        "Done. Parsed %d/%d. Failed: %d. Summary: %s",
        len(summary),
        len(pdf_paths),
        len(failures),
        summary_path,
    )
    if failures:
        logger.warning("Failed files (%d): %s", len(failures), failures)
    scanned = [s["file"] for s in summary if s["likely_scanned"]]
    if scanned:
        logger.warning("Likely-scanned (%d): %s", len(scanned), scanned)


if __name__ == "__main__":
    main()