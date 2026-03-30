#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from media_bundles import (
    MEDIA_RESOURCE_BASENAME,
    MEDIA_ROOT,
    ROOT,
    WORDPRESS_LIBRARY,
    WORDPRESS_ORIGINALS,
    build_front_matter,
    build_duplicate_groups,
    display_period_folder,
    ensure_unique_bundle_dir,
    list_media_items,
    load_wordpress_library,
    normalize_comma_list,
    prepare_section_root,
    prune_redundant_image_meta,
    reset_media_root,
    title_from_filename,
    write_markdown_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert the imported WordPress media library into one-media Hugo leaf bundles "
            "under content/media."
        )
    )
    parser.add_argument("--library", type=Path, default=WORDPRESS_LIBRARY)
    parser.add_argument("--originals-root", type=Path, default=WORDPRESS_ORIGINALS)
    parser.add_argument("--media-root", type=Path, default=MEDIA_ROOT)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete the target media root first before writing the migrated bundles.",
    )
    parser.add_argument(
        "--kind",
        choices=("all", "image", "video"),
        default="all",
        help="Only migrate one media kind instead of the whole library.",
    )
    return parser.parse_args()


def source_path_for_item(originals_root: Path, item: dict[str, object]) -> Path:
    relative_path = str(item.get("source_relative_path") or "")
    if not relative_path:
        raise ValueError(f"Media item {item.get('id')} is missing source_relative_path")
    return originals_root / Path(relative_path)


def bundle_slug_for_item(item: dict[str, object]) -> str:
    source_filename = str(item.get("original_filename") or "")
    return Path(source_filename).stem or "media"


def copy_original(source_path: Path, bundle_dir: Path) -> str:
    suffix = source_path.suffix.lower()
    target_name = f"{MEDIA_RESOURCE_BASENAME}{suffix}" if suffix else MEDIA_RESOURCE_BASENAME
    target_path = bundle_dir / target_name
    bundle_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)
    return target_name


def migrate_item(item: dict[str, object], originals_root: Path, media_root: Path) -> dict[str, str]:
    source_path = source_path_for_item(originals_root, item)
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    period_folder = display_period_folder(
        {
            "uploaded_at_local": item.get("uploaded_at_local"),
            "date": item.get("uploaded_at_local"),
        }
    )
    bundle_parent = media_root / period_folder
    bundle_dir = ensure_unique_bundle_dir(bundle_parent, bundle_slug_for_item(item))
    original_name = copy_original(source_path, bundle_dir)

    wordpress = dict(item.get("wordpress") or {})
    image_meta = dict(item.get("image_meta") or {})

    title = str(item.get("title") or "").strip() or title_from_filename(
        str(item.get("original_filename") or source_path.name)
    )
    description = str(item.get("description") or "").strip()

    front_matter = build_front_matter(
        title=title,
        date=str(item.get("uploaded_at_local") or "").replace(" ", "T") or None,
        lastmod=str(item.get("modified_at_local") or item.get("uploaded_at_local") or "").replace(" ", "T") or None,
        alt=str(item.get("alt") or "").strip() or None,
        caption=str(item.get("caption") or "").strip() or None,
        description=description or None,
        tags=normalize_comma_list(None),
        galleries=normalize_comma_list(None),
        credit=str(image_meta.get("credit") or "").strip() or None,
        location=None,
        original_filename=str(item.get("original_filename") or source_path.name),
        filesize=int(item["filesize"]) if item.get("filesize") else None,
        sha256=str(item.get("sha256") or "").strip() or None,
        uploaded_at_local=str(item.get("uploaded_at_local") or "").replace(" ", "T") or None,
        uploaded_at_gmt=str(item.get("uploaded_at_gmt") or "").replace(" ", "T") or None,
        modified_at_local=str(item.get("modified_at_local") or "").replace(" ", "T") or None,
        modified_at_gmt=str(item.get("modified_at_gmt") or "").replace(" ", "T") or None,
        wordpress_id=int(item["id"]) if item.get("id") else None,
        image_meta=image_meta if str(item.get("mime_type") or "").startswith("image/") else None,
        old_url=str(item.get("old_url") or wordpress.get("guid") or "").strip() or None,
    )
    front_matter, _ = prune_redundant_image_meta(front_matter, source_path)
    write_markdown_file(bundle_dir / "index.md", front_matter)
    return {
        "bundle_dir": bundle_dir.relative_to(media_root).as_posix(),
        "source": source_path.relative_to(originals_root).as_posix(),
        "resource": original_name,
    }


def iter_library_items(library: dict[str, object], kind: str) -> list[dict[str, object]]:
    raw_items = library.get("items")
    if isinstance(raw_items, list):
        items = [dict(item) for item in raw_items if isinstance(item, dict)]
    else:
        images = library.get("images")
        videos = library.get("videos")
        items = []
        if isinstance(images, list):
            items.extend(dict(item) for item in images if isinstance(item, dict))
        if isinstance(videos, list):
            items.extend(dict(item) for item in videos if isinstance(item, dict))

    if kind == "all":
        return items
    prefix = f"{kind}/"
    return [item for item in items if str(item.get("mime_type") or "").startswith(prefix)]


def main() -> int:
    args = parse_args()

    if args.replace:
        reset_media_root(args.media_root)
    prepare_section_root(args.media_root)

    library = (
        load_wordpress_library()
        if args.library == WORDPRESS_LIBRARY
        else json.loads(args.library.read_text(encoding="utf-8"))
    )

    migrated: list[dict[str, str]] = []
    for item in iter_library_items(library, args.kind):
        migrated.append(migrate_item(item, args.originals_root, args.media_root))

    items = list_media_items(args.media_root)
    duplicates = build_duplicate_groups(items)
    summary = {
        "imported_media": len(migrated),
        "bundle_items": len(items),
        "duplicate_original_groups": len(duplicates),
        "duplicate_original_files": sum(len(group) for group in duplicates.values()),
        "media_root": args.media_root.relative_to(ROOT).as_posix()
        if args.media_root.is_relative_to(ROOT)
        else args.media_root.as_posix(),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
