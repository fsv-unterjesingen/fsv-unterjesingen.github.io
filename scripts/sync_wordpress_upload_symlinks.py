#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from media_bundles import (
    MEDIA_ROOT,
    ROOT,
    list_bundles,
    load_bundle,
    relative_symlink_target,
    static_path_for_old_url,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create static/wp-content/uploads symlinks that mirror the legacy WordPress "
            "upload URLs and point at the current media bundle files."
        )
    )
    parser.add_argument("--media-root", type=Path, default=MEDIA_ROOT)
    parser.add_argument("--static-root", type=Path, default=ROOT / "static")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without modifying the filesystem.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    summary: dict[str, object] = {
        "bundles_scanned": 0,
        "created_symlinks": 0,
        "updated_symlinks": 0,
        "unchanged_symlinks": 0,
        "missing_old_url": 0,
        "blocked_paths": [],
        "conflicting_old_urls": [],
    }
    seen_paths: dict[Path, str] = {}

    for bundle_dir in list_bundles(args.media_root):
        item = load_bundle(bundle_dir)
        summary["bundles_scanned"] = int(summary["bundles_scanned"]) + 1

        old_url = str(item.get("old_url") or "").strip()
        if not old_url:
            summary["missing_old_url"] = int(summary["missing_old_url"]) + 1
            continue

        link_path = static_path_for_old_url(old_url, static_root=args.static_root)
        target_path = ROOT / str(item["original_path"])
        target_value = target_path.relative_to(ROOT).as_posix()

        prior_target = seen_paths.get(link_path)
        if prior_target is not None and prior_target != target_value:
            conflicts = list(summary["conflicting_old_urls"])
            conflicts.append(
                {
                    "old_url": old_url,
                    "static_path": link_path.relative_to(ROOT).as_posix(),
                    "target_a": prior_target,
                    "target_b": target_value,
                }
            )
            summary["conflicting_old_urls"] = conflicts
            continue
        seen_paths[link_path] = target_value

        relative_target = relative_symlink_target(link_path, target_path)
        replacing_existing_symlink = False

        if link_path.is_symlink():
            if os.readlink(link_path) == relative_target:
                summary["unchanged_symlinks"] = int(summary["unchanged_symlinks"]) + 1
                continue
            replacing_existing_symlink = True
            if not args.dry_run:
                link_path.unlink()
        elif link_path.exists():
            blocked = list(summary["blocked_paths"])
            blocked.append(link_path.relative_to(ROOT).as_posix())
            summary["blocked_paths"] = blocked
            continue

        if not args.dry_run:
            link_path.parent.mkdir(parents=True, exist_ok=True)
            link_path.symlink_to(relative_target)

        counter_key = "updated_symlinks" if replacing_existing_symlink else "created_symlinks"
        summary[counter_key] = int(summary[counter_key]) + 1

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
