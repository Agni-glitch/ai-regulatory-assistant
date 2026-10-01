"""
Data Ingestion — Cleaning and Normalization.

Reads parsed JSON from data/parsed/ and writes cleaned JSON to data/processed/,
preserving the per-page structure and schema.

Cleaning steps:
  1. Unicode normalization (smart quotes, ligatures, non-breaking spaces).
  2. Line-break hyphenation repair (join split words but KEEP the hyphen,
     so "research-\nbased" -> "research-based", not "researchbased").
  3. Frequency-based header/footer removal (identical lines repeated across
     many pages).
  4. Pattern-based artifact removal (URLs, print timestamps, page markers
     like "1/175", page labels like "PAGE | 12").
  5. Whitespace normalization (collapse blank lines and runs of spaces).
  6. Fix recital/article numbering glued to text: "(1)The" -> "(1) The".

Pure Python, standard library only. No external dependencies.

Run from the repo root:
    python ingestion/cleaner.py
    python ingestion/cleaner.py --in data/parsed --out data/processed
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import re
import unicodedata
from collections import Counter
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("cleaner")

# A line that appears on at least this fraction of a document's pages is
# treated as boilerplate (header/footer) and removed.
BOILERPLATE_PAGE_FRACTION = 0.5
# ...but only if the document has at least this many pages (avoids removing
# legitimate repeated lines in very short docs).
BOILERPLATE_MIN_PAGES = 4

# --- Pattern-based artifacts ------------------------------------------------

# Any URL.
URL_RE = re.compile(r"https?://\S+")
# Print timestamp like "6/10/26, 11:45 AM".
PRINT_TS_RE = re.compile(
    r"\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}:\d{2}\s*[AP]M", re.IGNORECASE
)
# Standalone page marker like "1/175" or "2 / 175".
PAGE_MARKER_RE = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
# Page label footer like "PAGE | 1" or "PAGE  |  12" (number varies per page).
PAGE_LABEL_RE = re.compile(r"^\s*PAGE\s*\|\s*\d+\s*$", re.IGNORECASE)

# Hyphen at end of line splitting a word: "develop-\nment".
# We KEEP the hyphen so compound words survive: "socio-economic", etc.
HYPHEN_BREAK_RE = re.compile(r"(\w+)-\n(\w+)")
# Recital/article number glued to following word: "(1)The" -> "(1) The".
GLUED_NUMBER_RE = re.compile(r"(\(\d+\))([A-Za-z])")

# --- Unicode normalization --------------------------------------------------

# Common typographic characters to replace after NFKC normalization.
UNICODE_REPLACEMENTS = {
    "\u00a0": " ",   # non-breaking space
    "\u2018": "'",   # left single quote
    "\u2019": "'",   # right single quote
    "\u201a": "'",   # single low-9 quote
    "\u201c": '"',   # left double quote
    "\u201d": '"',   # right double quote
    "\u201e": '"',   # double low-9 quote
    "\u2013": "-",   # en dash
    "\u2014": "-",   # em dash
    "\u2212": "-",   # minus sign
    "\u2026": "...",  # ellipsis
    "\ufb00": "ff",  # ff ligature
    "\ufb01": "fi",  # fi ligature
    "\ufb02": "fl",  # fl ligature
    "\ufb03": "ffi",  # ffi ligature
    "\ufb04": "ffl",  # ffl ligature
}


def normalize_unicode(text: str) -> str:
    """Normalize unicode: NFKC + replace common typographic characters."""
    text = unicodedata.normalize("NFKC", text)
    for bad, good in UNICODE_REPLACEMENTS.items():
        text = text.replace(bad, good)
    return text


def find_boilerplate_lines(pages: list[dict]) -> set[str]:
    """
    Identify lines that repeat across many pages (headers/footers).

    Only catches lines that are IDENTICAL across pages. Page labels whose
    number changes (e.g. "PAGE | 1", "PAGE | 2") are handled separately by
    PAGE_LABEL_RE, not here.

    Returns a set of stripped line strings to remove.
    """
    num_pages = len(pages)
    if num_pages < BOILERPLATE_MIN_PAGES:
        return set()

    counter: Counter[str] = Counter()
    for page in pages:
        # Count each unique line once per page so a line repeated within a
        # single page doesn't inflate the cross-page count.
        seen: set[str] = set()
        for line in page.get("text", "").splitlines():
            stripped = line.strip()
            if len(stripped) < 5:
                continue
            if stripped not in seen:
                seen.add(stripped)
                counter[stripped] += 1

    threshold = max(
        BOILERPLATE_MIN_PAGES, int(num_pages * BOILERPLATE_PAGE_FRACTION)
    )
    return {line for line, count in counter.items() if count >= threshold}


def clean_line(line: str) -> str:
    """Apply pattern-based artifact removal to a single (already stripped) line."""
    if PAGE_MARKER_RE.match(line):
        return ""
    if PAGE_LABEL_RE.match(line):
        return ""
    if PRINT_TS_RE.search(line):
        # Drop the timestamp portion; keep anything else on the line.
        line = PRINT_TS_RE.sub("", line)
    # Strip URLs (often trailing artifacts from print-to-PDF footers).
    line = URL_RE.sub("", line)
    return line


def clean_page_text(text: str, boilerplate: set[str]) -> str:
    """Clean a single page's text."""
    # 1. Repair hyphenation BEFORE splitting into lines (keep the hyphen).
    text = HYPHEN_BREAK_RE.sub(r"\1-\2", text)
    # 2. Normalize unicode.
    text = normalize_unicode(text)

    # 3. Line-by-line filtering.
    kept_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in boilerplate:
            continue
        cleaned = clean_line(stripped).strip()
        if cleaned:
            kept_lines.append(cleaned)

    joined = "\n".join(kept_lines)

    # 4. Fix glued recital/article numbering: "(1)The" -> "(1) The".
    joined = GLUED_NUMBER_RE.sub(r"\1 \2", joined)
    # 5. Collapse 3+ newlines down to a single paragraph break.
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    # 6. Collapse runs of spaces/tabs.
    joined = re.sub(r"[ \t]{2,}", " ", joined)

    return joined.strip()


def clean_document(doc: dict) -> dict:
    """Clean all pages of a parsed document, returning a new dict."""
    pages = doc.get("pages", [])
    boilerplate = find_boilerplate_lines(pages)

    cleaned_pages: list[dict] = []
    total_chars = 0
    for page in pages:
        cleaned_text = clean_page_text(page.get("text", ""), boilerplate)
        total_chars += len(cleaned_text)
        cleaned_pages.append(
            {
                "page_number": page.get("page_number"),
                "char_count": len(cleaned_text),
                "text": cleaned_text,
            }
        )

    result = dict(doc)  # shallow copy of top-level metadata
    result["pages"] = cleaned_pages
    result["cleaning"] = {
        "boilerplate_lines_removed": sorted(boilerplate),
        "total_chars_before": doc.get("quality", {}).get("total_chars"),
        "total_chars_after": total_chars,
        "cleaned_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }
    return result


def main() -> None:
    arg_parser = argparse.ArgumentParser(description="Clean parsed PDF JSON.")
    arg_parser.add_argument(
        "--in", dest="in_dir", default="data/parsed",
        help="Input parsed-JSON root.",
    )
    arg_parser.add_argument(
        "--out", dest="out_dir", default="data/processed",
        help="Output cleaned-JSON root.",
    )
    args = arg_parser.parse_args()

    in_root = Path(args.in_dir).resolve()
    out_root = Path(args.out_dir).resolve()

    if not in_root.exists():
        logger.error("Input directory does not exist: %s", in_root)
        raise SystemExit(1)

    out_root.mkdir(parents=True, exist_ok=True)

    json_paths = sorted(
        p for p in in_root.rglob("*.json") if p.name != "_parse_summary.json"
    )
    if not json_paths:
        logger.warning("No parsed JSON found under %s", in_root)
        return

    logger.info("Found %d parsed JSON files under %s", len(json_paths), in_root)

    summary: list[dict] = []
    for json_path in json_paths:
        try:
            doc = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.exception("Failed to read %s: %s", json_path, exc)
            continue

        cleaned = clean_document(doc)

        rel = json_path.relative_to(in_root)
        out_path = out_root / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        before = cleaned["cleaning"]["total_chars_before"] or 0
        after = cleaned["cleaning"]["total_chars_after"]
        removed_lines = len(cleaned["cleaning"]["boilerplate_lines_removed"])
        summary.append(
            {
                "file": cleaned.get("source_path", str(rel)),
                "chars_before": before,
                "chars_after": after,
                "boilerplate_lines_removed": removed_lines,
            }
        )
        logger.info(
            "Cleaned %-55s %d -> %d chars (boilerplate lines removed: %d)",
            cleaned.get("source_path", str(rel)),
            before,
            after,
            removed_lines,
        )

    summary_path = out_root / "_clean_summary.json"
    summary_path.write_text(
        json.dumps(
            {"total": len(summary), "files": summary},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info(
        "Done. Cleaned %d files. Summary: %s", len(summary), summary_path
    )


if __name__ == "__main__":
    main()