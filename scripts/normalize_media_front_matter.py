#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from media_bundles import (
    MEDIA_ROOT,
    canonicalize_bundle_front_matter,
    list_bundles,
    original_file_for_bundle,
    prune_redundant_image_meta,
    read_markdown_file,
    write_markdown_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize media bundle front matter by removing redundant derived fields, renaming "
            "`source_filename` to `original_filename`, and dropping empty/default values."
        )
    )
    parser.add_argument("--root", type=Path, default=MEDIA_ROOT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report the changes without rewriting any files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    summary = {
        "bundles_scanned": 0,
        "bundles_updated": 0,
        "renamed_source_filename": 0,
        "image_meta_removed": 0,
        "image_meta_trimmed": 0,
        "removed_top_level_fields": {},
    }
    removed_top_level_fields: Counter[str] = Counter()

    for bundle_dir in list_bundles(args.root):
        index_path = bundle_dir / "index.md"
        original_path = original_file_for_bundle(bundle_dir)
        if original_path is None:
            continue

        front_matter, body = read_markdown_file(index_path)
        summary["bundles_scanned"] += 1

        if "source_filename" in front_matter:
            summary["renamed_source_filename"] += 1

        before_keys = set(front_matter.keys())
        before_image_meta = dict(front_matter.get("image_meta") or {})

        updated = canonicalize_bundle_front_matter(front_matter)
        updated, _ = prune_redundant_image_meta(updated, original_path)

        after_keys = set(updated.keys())
        for key in sorted(before_keys - after_keys):
            removed_top_level_fields[key] += 1

        after_image_meta = dict(updated.get("image_meta") or {})
        if before_image_meta and not after_image_meta:
            summary["image_meta_removed"] += 1
        elif before_image_meta and after_image_meta and before_image_meta != after_image_meta:
            summary["image_meta_trimmed"] += 1

        if updated == front_matter:
            continue

        summary["bundles_updated"] += 1
        if not args.dry_run:
            write_markdown_file(index_path, updated, body)

    summary["removed_top_level_fields"] = dict(sorted(removed_top_level_fields.items()))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
