#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path

from media_bundles import (
    MEDIA_ROOT,
    list_bundles,
    original_file_for_bundle,
    prune_redundant_image_meta,
    read_markdown_file,
    write_markdown_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Remove `image_meta` from media bundle front matter when it contains only default "
            "WordPress placeholders or when its non-default values are recoverable from the "
            "original image file."
        )
    )
    parser.add_argument("--root", type=Path, default=MEDIA_ROOT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify all bundles and print the summary without rewriting any files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    summary = {
        "bundles_scanned": 0,
        "image_meta_removed": 0,
        "removed_empty": 0,
        "removed_embedded": 0,
        "kept_mismatched": 0,
        "missing_original": 0,
        "already_missing": 0,
    }

    for bundle_dir in list_bundles(args.root):
        index_path = bundle_dir / "index.md"
        front_matter, body = read_markdown_file(index_path)
        summary["bundles_scanned"] += 1

        if "image_meta" not in front_matter:
            summary["already_missing"] += 1
            continue

        original_path = original_file_for_bundle(bundle_dir)
        if original_path is None:
            summary["missing_original"] += 1
            continue

        updated_front_matter, report = prune_redundant_image_meta(front_matter, original_path)
        if not report.get("removable"):
            summary["kept_mismatched"] += 1
            continue

        summary["image_meta_removed"] += 1
        reason = str(report.get("reason") or "")
        if reason == "empty":
            summary["removed_empty"] += 1
        elif reason == "embedded":
            summary["removed_embedded"] += 1

        if args.dry_run:
            continue
        write_markdown_file(index_path, updated_front_matter, body)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
