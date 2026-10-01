"""
Find duplicate PDFs (and any files) by content hash.

Scans a folder recursively, computes a SHA-256 hash of each file's bytes,
and reports groups of files that are byte-for-byte identical.

SAFE: this script only REPORTS duplicates. It does not delete anything.
It also prints ready-to-use delete commands for you to review and run
manually.

Run from the repo root:
    python ingestion/find_duplicates.py
    python ingestion/find_duplicates.py --dir document --ext .pdf
    python ingestion/find_duplicates.py --dir document        (all files)
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

# Read files in chunks so large PDFs don't blow up memory.
CHUNK_SIZE = 1024 * 1024  # 1 MB


def hash_file(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK_SIZE), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Find duplicate files by hash.")
    parser.add_argument("--dir", default="document", help="Folder to scan.")
    parser.add_argument(
        "--ext",
        default="",
        help="Only scan this extension, e.g. .pdf (default: all files).",
    )
    args = parser.parse_args()

    root = Path(args.dir).resolve()
    if not root.exists():
        raise SystemExit(f"Directory does not exist: {root}")

    ext = args.ext.lower()

    # Collect candidate files.
    files = [p for p in root.rglob("*") if p.is_file()]
    if ext:
        files = [p for p in files if p.suffix.lower() == ext]

    print(f"Scanning {len(files)} files under {root} ...\n")

    # Group files by hash.
    by_hash: dict[str, list[Path]] = defaultdict(list)
    for path in files:
        try:
            digest = hash_file(path)
        except Exception as exc:
            print(f"  ! Could not read {path}: {exc}")
            continue
        by_hash[digest].append(path)

    # Keep only groups with more than one file (= duplicates).
    dup_groups = {h: paths for h, paths in by_hash.items() if len(paths) > 1}

    if not dup_groups:
        print("No duplicates found. Your corpus is clean.")
        return

    total_dupes = sum(len(paths) - 1 for paths in dup_groups.values())
    wasted = 0

    print(f"Found {len(dup_groups)} duplicate group(s), "
          f"{total_dupes} redundant file(s):\n")

    report: list[dict] = []
    delete_commands: list[str] = []

    for i, (digest, paths) in enumerate(dup_groups.items(), start=1):
        paths_sorted = sorted(paths, key=lambda p: str(p))
        keep = paths_sorted[0]          # keep the first alphabetically
        remove = paths_sorted[1:]       # suggest removing the rest
        size = keep.stat().st_size
        wasted += size * len(remove)

        print(f"Group {i}  (sha256: {digest[:16]}...,  {size:,} bytes each)")
        for p in paths_sorted:
            rel = p.relative_to(root)
            tag = "KEEP  " if p == keep else "DELETE"
            print(f"    [{tag}] {rel}")
        print()

        report.append(
            {
                "hash": digest,
                "size_bytes": size,
                "keep": str(keep.relative_to(root)),
                "delete": [str(p.relative_to(root)) for p in remove],
            }
        )
        for p in remove:
            # PowerShell-friendly delete command (quoted for spaces).
            delete_commands.append(f'Remove-Item "{p}"')

    print(f"Approx. wasted space from duplicates: {wasted:,} bytes "
          f"(~{wasted / 1_048_576:.1f} MB)\n")

    # Write a machine-readable report.
    report_path = root.parent / "duplicates_report.json"
    report_path.write_text(
        json.dumps({"groups": report, "wasted_bytes": wasted},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Report written to: {report_path}\n")

    # Print ready-to-run delete commands (review before running!).
    print("=" * 60)
    print("SUGGESTED DELETE COMMANDS (PowerShell) — REVIEW BEFORE RUNNING:")
    print("=" * 60)
    for cmd in delete_commands:
        print(cmd)


if __name__ == "__main__":
    main()