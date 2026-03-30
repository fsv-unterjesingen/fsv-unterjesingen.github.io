#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import math
import mimetypes
import os
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

import yaml

try:
    from PIL import ExifTags, Image
except ImportError:  # pragma: no cover - available in this workspace
    ExifTags = None
    Image = None


ROOT = Path(__file__).resolve().parents[1]
MEDIA_ROOT = ROOT / "content" / "media"
WORDPRESS_LIBRARY = ROOT / "data" / "wordpress_media" / "library.json"
WORDPRESS_ORIGINALS = ROOT / "assets" / "images" / "wordpress-media" / "originals"
MEDIA_RESOURCE_BASENAME = "media"

FRONT_MATTER_ORDER = [
    "title",
    "date",
    "lastmod",
    "alt",
    "caption",
    "description",
    "tags",
    "galleries",
    "credit",
    "location",
    "original_filename",
    "old_url",
    "wordpress_id",
    "image_meta",
    "build",
]

SECTION_FRONT_MATTER = {
    "title": "Media",
    "build": {"render": "never", "list": "always", "publishResources": True},
}

WORDPRESS_IMAGE_META_TEXT_KEYS = {"credit", "camera", "caption", "copyright", "title"}
WORDPRESS_IMAGE_META_NUMERIC_KEYS = {
    "aperture",
    "created_timestamp",
    "focal_length",
    "iso",
    "shutter_speed",
    "orientation",
}
WORDPRESS_IMAGE_META_KEYS = WORDPRESS_IMAGE_META_TEXT_KEYS | WORDPRESS_IMAGE_META_NUMERIC_KEYS | {
    "keywords"
}


def media_kind_for_mime(mime_type: str | None) -> str:
    clean = str(mime_type or "").strip().lower()
    if clean.startswith("image/"):
        return "image"
    if clean.startswith("video/"):
        return "video"
    return "file"


def media_kind_for_path(path: Path) -> str:
    return media_kind_for_mime(mimetypes.guess_type(path.name)[0])


def title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    cleaned = re.sub(r"[_-]+", " ", stem).strip()
    if not cleaned:
        return "Untitled media"
    return cleaned[0].upper() + cleaned[1:]


def parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        return {}, text

    end_index = text.find("\n---\n", 4)
    if end_index < 0:
        return {}, text

    front_matter = text[4:end_index]
    body = text[end_index + 5 :]
    data = yaml.safe_load(front_matter) or {}
    if not isinstance(data, dict):
        raise ValueError("Expected YAML front matter to deserialize to a mapping")
    return data, body


def dump_front_matter(data: dict[str, Any], body: str = "") -> str:
    ordered: dict[str, Any] = {}
    for key in FRONT_MATTER_ORDER:
        if key in data:
            ordered[key] = data[key]
    for key, value in data.items():
        if key not in ordered:
            ordered[key] = value

    payload = yaml.safe_dump(
        ordered,
        sort_keys=False,
        allow_unicode=True,
        width=100,
        default_flow_style=False,
    ).strip()

    clean_body = body.lstrip("\n")
    if clean_body:
        return f"---\n{payload}\n---\n\n{clean_body}"
    return f"---\n{payload}\n---\n"


def write_markdown_file(path: Path, front_matter: dict[str, Any], body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_front_matter(front_matter, body), encoding="utf-8")


def read_markdown_file(path: Path) -> tuple[dict[str, Any], str]:
    return parse_front_matter(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_exif_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, tuple):
        return [_normalize_exif_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _normalize_exif_value(item) for key, item in value.items()}
    if isinstance(value, (int, float, str, bool)) or value is None:
        return value
    return str(value)


def _extract_pillow_metadata(image: Any) -> dict[str, Any]:
    if ExifTags is None:
        return {}

    metadata: dict[str, Any] = {}
    try:
        exif = image.getexif()
    except Exception:
        return metadata

    for key, value in exif.items():
        tag = ExifTags.TAGS.get(key, str(key))
        metadata[str(tag)] = _normalize_exif_value(value)
    return metadata


def probe_media_file(path: Path) -> dict[str, Any]:
    details = {
        "filesize": path.stat().st_size,
        "width": None,
        "height": None,
        "mime_type": None,
        "sha256": sha256_file(path),
        "image_meta": {},
    }

    if Image is None or media_kind_for_path(path) != "image":
        return details

    try:
        with Image.open(path) as image:
            details["width"], details["height"] = image.size
            details["mime_type"] = Image.MIME.get(image.format)
            details["image_meta"] = _extract_pillow_metadata(image)
    except Exception:
        pass

    return details


def probe_image(path: Path) -> dict[str, Any]:
    return probe_media_file(path)


def inspect_media_file(path: Path) -> dict[str, Any]:
    details = {
        "filesize": path.stat().st_size,
        "width": None,
        "height": None,
        "mime_type": mimetypes.guess_type(path.name)[0],
    }

    if Image is None or media_kind_for_path(path) != "image":
        return details

    try:
        with Image.open(path) as image:
            details["width"], details["height"] = image.size
            details["mime_type"] = Image.MIME.get(image.format) or details["mime_type"]
    except Exception:
        pass

    return details


def inspect_image_file(path: Path) -> dict[str, Any]:
    return inspect_media_file(path)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None

    candidates = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d",
    ]
    for candidate in candidates:
        try:
            return datetime.strptime(value, candidate)
        except ValueError:
            continue
    return None


def parse_exif_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    for candidate in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, candidate)
        except ValueError:
            continue
    return None


def to_datetime_local_value(value: str | None) -> str:
    timestamp = parse_timestamp(value)
    if timestamp is None:
        return ""
    return timestamp.strftime("%Y-%m-%dT%H:%M:%S")


def format_timestamp(timestamp: datetime | None) -> str | None:
    if timestamp is None:
        return None
    return timestamp.strftime("%Y-%m-%dT%H:%M:%S")


def _clean_image_meta_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\x00", "").strip()


def _is_default_image_meta_value(key: str, value: Any) -> bool:
    if value is None or value is False:
        return True

    if key == "keywords":
        if isinstance(value, list):
            return not any(_clean_image_meta_text(item) for item in value)
        return not _clean_image_meta_text(value)

    if key in WORDPRESS_IMAGE_META_TEXT_KEYS:
        return not _clean_image_meta_text(value)

    try:
        return float(_clean_image_meta_text(value) or "0") == 0.0
    except (TypeError, ValueError):
        return not _clean_image_meta_text(value)


def meaningful_image_meta_entries(image_meta: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(image_meta or {})
    return {
        key: value
        for key, value in payload.items()
        if key in WORDPRESS_IMAGE_META_KEYS and not _is_default_image_meta_value(key, value)
    }


def _decode_xp_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-16-le", errors="ignore").rstrip("\x00").strip()
    if isinstance(value, (list, tuple)):
        try:
            return (
                bytes(int(item) for item in value)
                .decode("utf-16-le", errors="ignore")
                .rstrip("\x00")
                .strip()
            )
        except (TypeError, ValueError):
            return "".join(chr(int(item)) for item in value if int(item)).strip()
    return _clean_image_meta_text(value)


def _decode_user_comment(value: Any) -> str:
    if not isinstance(value, bytes):
        return _clean_image_meta_text(value)

    raw = value
    for prefix in (b"ASCII\x00\x00\x00", b"JIS\x00\x00\x00\x00\x00", b"UNICODE\x00"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break

    for encoding in ("utf-8", "utf-16-be", "utf-16-le", "latin-1"):
        try:
            return raw.decode(encoding, errors="ignore").strip(" \x00\ufeff")
        except Exception:
            continue
    return raw.decode("utf-8", errors="ignore").strip(" \x00\ufeff")


def _extract_named_ifd(exif: Any, ifd_name: str) -> dict[str, Any]:
    if ExifTags is None or not hasattr(exif, "get_ifd"):
        return {}

    ifd_id = getattr(ExifTags.IFD, ifd_name, None)
    if ifd_id is None:
        return {}

    try:
        raw_ifd = exif.get_ifd(ifd_id)
    except Exception:
        return {}

    named_ifd: dict[str, Any] = {}
    for key, value in raw_ifd.items():
        tag = ExifTags.TAGS.get(key, str(key))
        if isinstance(value, bytes):
            named_ifd[str(tag)] = value
        else:
            named_ifd[str(tag)] = _normalize_exif_value(value)
    return named_ifd


def _parse_embedded_exif_timestamp(value: str | None) -> datetime | None:
    if not value or value.startswith("0000:00:00"):
        return None
    for candidate in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, candidate)
        except ValueError:
            continue
    return None


def _safe_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def embedded_wordpress_image_meta_for_image(path: Path) -> dict[str, Any]:
    if Image is None:
        return {}

    try:
        with Image.open(path) as image:
            exif = image.getexif()
    except Exception:
        return {}

    top_level = {
        str(ExifTags.TAGS.get(key, str(key))): _normalize_exif_value(value)
        for key, value in exif.items()
    }
    exif_ifd = _extract_named_ifd(exif, "Exif")

    embedded: dict[str, Any] = {}
    model = _clean_image_meta_text(top_level.get("Model"))
    if model:
        embedded["camera"] = model

    artist = _clean_image_meta_text(top_level.get("Artist"))
    xp_author = _clean_image_meta_text(_decode_xp_text(top_level.get("XPAuthor")))
    if artist:
        embedded["credit"] = artist
    elif xp_author:
        embedded["credit"] = xp_author

    copyright_value = _clean_image_meta_text(top_level.get("Copyright"))
    if copyright_value:
        embedded["copyright"] = copyright_value

    image_description = _clean_image_meta_text(top_level.get("ImageDescription"))
    xp_title = _clean_image_meta_text(_decode_xp_text(top_level.get("XPTitle")))
    xp_comment = _clean_image_meta_text(_decode_xp_text(top_level.get("XPComment")))
    user_comment = _clean_image_meta_text(_decode_user_comment(exif_ifd.get("UserComment")))

    if image_description:
        embedded["title"] = image_description
    elif xp_title:
        embedded["title"] = xp_title

    if user_comment:
        embedded["caption"] = user_comment
    elif image_description:
        embedded["caption"] = image_description
    elif xp_comment:
        embedded["caption"] = xp_comment

    created_at = _parse_embedded_exif_timestamp(
        _clean_image_meta_text(exif_ifd.get("DateTimeOriginal"))
        or _clean_image_meta_text(exif_ifd.get("DateTimeDigitized"))
        or _clean_image_meta_text(top_level.get("DateTime"))
    )
    if created_at is not None:
        embedded["created_timestamp"] = int(created_at.replace(tzinfo=timezone.utc).timestamp())

    orientation = _safe_float(top_level.get("Orientation"))
    if orientation is not None:
        embedded["orientation"] = int(orientation)

    numeric_mappings = (
        ("aperture", "FNumber"),
        ("focal_length", "FocalLength"),
        ("iso", "ISOSpeedRatings"),
        ("shutter_speed", "ExposureTime"),
    )
    for image_meta_key, exif_key in numeric_mappings:
        raw_value = exif_ifd.get(exif_key)
        if raw_value is None and image_meta_key == "iso":
            raw_value = exif_ifd.get("PhotographicSensitivity")
        numeric = _safe_float(raw_value)
        if numeric is not None:
            embedded[image_meta_key] = numeric

    keywords = _clean_image_meta_text(_decode_xp_text(top_level.get("XPKeywords")))
    if keywords:
        embedded["keywords"] = [
            part.strip() for part in keywords.replace(";", ",").split(",") if part.strip()
        ]

    return embedded


def _image_meta_values_match(key: str, front_value: Any, embedded_value: Any) -> bool:
    if embedded_value is None:
        return False

    if key == "keywords":
        if isinstance(front_value, str):
            front_items = [
                part.strip() for part in front_value.replace(";", ",").split(",") if part.strip()
            ]
        else:
            front_items = [
                _clean_image_meta_text(item)
                for item in list(front_value or [])
                if _clean_image_meta_text(item)
            ]
        if isinstance(embedded_value, str):
            embedded_items = [
                part.strip()
                for part in embedded_value.replace(";", ",").split(",")
                if part.strip()
            ]
        else:
            embedded_items = [
                _clean_image_meta_text(item)
                for item in list(embedded_value or [])
                if _clean_image_meta_text(item)
            ]
        return front_items == embedded_items

    if key in WORDPRESS_IMAGE_META_TEXT_KEYS:
        return _clean_image_meta_text(front_value) == _clean_image_meta_text(embedded_value)

    if key == "orientation":
        front_numeric = _safe_float(front_value)
        embedded_numeric = _safe_float(embedded_value)
        if front_numeric is None or embedded_numeric is None:
            return False
        return int(front_numeric) == int(embedded_numeric)

    if key == "created_timestamp":
        front_numeric = _safe_float(front_value)
        embedded_numeric = _safe_float(embedded_value)
        if front_numeric is None or embedded_numeric is None:
            return False
        return abs(int(front_numeric) - int(embedded_numeric)) <= 1

    front_numeric = _safe_float(front_value)
    embedded_numeric = _safe_float(embedded_value)
    if front_numeric is None or embedded_numeric is None:
        return False
    return math.isclose(front_numeric, embedded_numeric, rel_tol=1e-6, abs_tol=1e-9)


def redundant_image_meta_report(
    image_meta: dict[str, Any] | None, original_path: Path
) -> dict[str, Any]:
    if image_meta is None:
        return {"removable": False, "reason": "missing", "mismatches": []}

    meaningful = meaningful_image_meta_entries(image_meta)
    if not meaningful:
        return {"removable": True, "reason": "empty", "mismatches": []}

    embedded = embedded_wordpress_image_meta_for_image(original_path)
    mismatches: list[dict[str, Any]] = []
    for key, value in meaningful.items():
        embedded_value = embedded.get(key)
        if _image_meta_values_match(key, value, embedded_value):
            continue
        mismatches.append({"key": key, "front": value, "embedded": embedded_value})

    if mismatches:
        return {"removable": False, "reason": "mismatched", "mismatches": mismatches}
    return {"removable": True, "reason": "embedded", "mismatches": []}


def prune_redundant_image_meta(
    front_matter: dict[str, Any], original_path: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    updated = dict(front_matter)
    report = redundant_image_meta_report(updated.get("image_meta"), original_path)
    if report.get("removable"):
        updated.pop("image_meta", None)
    return updated, report


def canonicalize_bundle_front_matter(front_matter: dict[str, Any]) -> dict[str, Any]:
    updated = dict(front_matter)
    legacy_build = updated.pop("_build", None)
    if legacy_build is not None and "build" not in updated:
        updated["build"] = legacy_build

    legacy_wordpress = dict(updated.pop("wordpress", {}) or {})
    legacy_guid = _clean_image_meta_text(legacy_wordpress.get("guid"))
    current_old_url = _clean_image_meta_text(updated.get("old_url"))
    if legacy_guid and not current_old_url:
        updated["old_url"] = legacy_guid
    elif current_old_url:
        updated["old_url"] = current_old_url

    source_filename = _clean_image_meta_text(updated.pop("source_filename", None))
    current_original_filename = _clean_image_meta_text(updated.get("original_filename"))
    if source_filename:
        updated["original_filename"] = source_filename
    elif not current_original_filename:
        updated.pop("original_filename", None)

    for redundant_key in ("source_relative_path", "mime_type", "width", "height", "filesize", "sha256"):
        updated.pop(redundant_key, None)

    date_value = _clean_image_meta_text(updated.get("date"))
    lastmod_value = _clean_image_meta_text(updated.get("lastmod"))
    if date_value and date_value == _clean_image_meta_text(updated.get("uploaded_at_local")):
        updated.pop("uploaded_at_local", None)
        updated.pop("uploaded_at_gmt", None)
    if lastmod_value and lastmod_value == _clean_image_meta_text(updated.get("modified_at_local")):
        updated.pop("modified_at_local", None)
        updated.pop("modified_at_gmt", None)

    if "image_meta" in updated:
        meaningful_image_meta = meaningful_image_meta_entries(updated.get("image_meta"))
        if meaningful_image_meta:
            updated["image_meta"] = meaningful_image_meta
        else:
            updated.pop("image_meta", None)

    for key in list(updated.keys()):
        if key == "build":
            continue
        value = updated[key]
        if value is None:
            updated.pop(key, None)
        elif isinstance(value, str) and not value.strip():
            updated.pop(key, None)
        elif isinstance(value, (list, dict)) and not value:
            updated.pop(key, None)

    return updated


def display_period_folder(metadata: dict[str, Any]) -> str:
    timestamp = parse_timestamp(
        str(metadata.get("uploaded_at_local") or metadata.get("date") or "") or None
    )
    if timestamp is None:
        now = datetime.now()
        return now.strftime("%Y-%m")
    return timestamp.strftime("%Y-%m")


def ensure_unique_bundle_dir(parent: Path, base_slug: str) -> Path:
    candidate = parent / base_slug
    if not candidate.exists():
        return candidate

    suffix = 2
    while True:
        next_candidate = parent / f"{base_slug}-{suffix}"
        if not next_candidate.exists():
            return next_candidate
        suffix += 1


def original_file_for_bundle(bundle_dir: Path) -> Path | None:
    for basename in (f"{MEDIA_RESOURCE_BASENAME}.", "original."):
        for candidate in sorted(bundle_dir.iterdir()):
            if candidate.is_file() and candidate.name.startswith(basename):
                return candidate
    return None


def relative_bundle_path(bundle_dir: Path) -> str:
    return bundle_dir.relative_to(ROOT).as_posix()


def relative_media_path(bundle_dir: Path) -> str:
    return bundle_dir.relative_to(MEDIA_ROOT).as_posix()


def gallery_thumbnail_path(bundle_dir: Path) -> str:
    return f"/thumb/{relative_media_path(bundle_dir)}"


def gallery_media_file_path(bundle_dir: Path) -> str:
    return f"/media-file/{relative_media_path(bundle_dir)}"


def load_bundle(bundle_dir: Path) -> dict[str, Any]:
    index_path = bundle_dir / "index.md"
    metadata, body = read_markdown_file(index_path)
    original_path = original_file_for_bundle(bundle_dir)
    if original_path is None:
        raise FileNotFoundError(f"Missing original image in {bundle_dir}")

    metadata = dict(metadata)
    file_details = inspect_media_file(original_path)
    metadata["description"] = metadata.get("description") or body.strip() or None
    metadata["bundle_dir"] = relative_bundle_path(bundle_dir)
    metadata["media_path"] = relative_media_path(bundle_dir)
    metadata["image_url"] = gallery_media_file_path(bundle_dir)
    metadata["thumb_url"] = gallery_thumbnail_path(bundle_dir)
    metadata["original_path"] = original_path.relative_to(ROOT).as_posix()
    metadata["resource_filename"] = original_path.name
    metadata["original_filename"] = (
        metadata.get("source_filename") or metadata.get("original_filename") or original_path.name
    )
    metadata["old_url"] = metadata.get("old_url") or dict(metadata.get("wordpress") or {}).get("guid") or ""
    metadata["width"] = metadata.get("width") or file_details.get("width")
    metadata["height"] = metadata.get("height") or file_details.get("height")
    metadata["mime_type"] = metadata.get("mime_type") or file_details.get("mime_type")
    metadata["media_kind"] = media_kind_for_mime(metadata.get("mime_type"))
    metadata["filesize"] = metadata.get("filesize") or file_details.get("filesize")
    metadata["sha256"] = metadata.get("sha256") or sha256_file(original_path)
    metadata["tags"] = list(metadata.get("tags") or [])
    metadata["galleries"] = list(metadata.get("galleries") or [])
    metadata["title"] = metadata.get("title") or title_from_filename(
        str(metadata.get("original_filename") or original_path.name)
    )
    metadata["date"] = to_datetime_local_value(str(metadata.get("date") or "") or None)
    metadata["lastmod"] = to_datetime_local_value(str(metadata.get("lastmod") or "") or None)
    metadata["description"] = metadata.get("description") or ""
    metadata["alt"] = metadata.get("alt") or ""
    metadata["caption"] = metadata.get("caption") or ""
    metadata["credit"] = metadata.get("credit") or ""
    metadata["location"] = metadata.get("location") or ""
    metadata["missing_alt"] = metadata["media_kind"] == "image" and not bool(
        str(metadata.get("alt") or "").strip()
    )
    metadata.pop("build", None)
    metadata.pop("wordpress", None)
    return metadata


def list_bundles(root: Path = MEDIA_ROOT) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path.parent for path in root.rglob("index.md") if path.name == "index.md")


def list_media_items(root: Path = MEDIA_ROOT) -> list[dict[str, Any]]:
    items = [load_bundle(bundle_dir) for bundle_dir in list_bundles(root)]
    items.sort(
        key=lambda item: (
            item.get("date") or "",
            item.get("title") or "",
            item.get("bundle_dir") or "",
        )
    )
    return items


def prepare_section_root(root: Path = MEDIA_ROOT) -> None:
    root.mkdir(parents=True, exist_ok=True)
    section_index = root / "_index.md"
    if not section_index.exists():
        write_markdown_file(section_index, SECTION_FRONT_MATTER)


def build_front_matter(
    *,
    title: str,
    date: str | None,
    lastmod: str | None,
    alt: str | None,
    caption: str | None,
    description: str | None,
    tags: list[str] | None,
    galleries: list[str] | None,
    credit: str | None,
    location: str | None,
    original_filename: str,
    filesize: int | None,
    sha256: str | None,
    uploaded_at_local: str | None,
    uploaded_at_gmt: str | None,
    modified_at_local: str | None,
    modified_at_gmt: str | None,
    wordpress_id: int | None,
    image_meta: dict[str, Any] | None,
    old_url: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": title,
        "date": date,
        "lastmod": lastmod,
        "alt": alt or "",
        "caption": caption or "",
        "description": description or "",
        "tags": tags or [],
        "galleries": galleries or [],
        "credit": credit or "",
        "location": location or "",
        "original_filename": str(original_filename or "").strip() or None,
        "filesize": filesize,
        "sha256": sha256,
        "uploaded_at_local": uploaded_at_local,
        "uploaded_at_gmt": uploaded_at_gmt,
        "modified_at_local": modified_at_local,
        "modified_at_gmt": modified_at_gmt,
        "old_url": str(old_url or "").strip() or None,
        "wordpress_id": wordpress_id,
        "build": {"render": "never", "list": "always", "publishResources": True},
    }
    if image_meta is not None:
        payload["image_meta"] = image_meta
    return canonicalize_bundle_front_matter(
        {key: value for key, value in payload.items() if value is not None}
    )


def normalize_comma_list(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = [part.strip() for part in str(value).split(",")]
    return [item for item in items if item]


def save_bundle_metadata(
    bundle_dir: Path,
    metadata_updates: dict[str, Any],
    *,
    touch_lastmod: bool = True,
) -> None:
    index_path = bundle_dir / "index.md"
    front_matter, body = read_markdown_file(index_path)
    body_text = body

    if "description" in metadata_updates:
        body_text = ""

    merged = dict(front_matter)
    merged.update(metadata_updates)
    merged["description"] = metadata_updates.get("description", merged.get("description", "")) or ""
    merged["tags"] = normalize_comma_list(merged.get("tags"))
    merged["galleries"] = normalize_comma_list(merged.get("galleries"))

    if touch_lastmod:
        merged["lastmod"] = format_timestamp(datetime.now())

    write_markdown_file(index_path, canonicalize_bundle_front_matter(merged), body_text)


def _prune_empty_parents(start: Path, stop_at: Path) -> None:
    current = start
    while current != stop_at and current.exists():
        try:
            next(current.iterdir())
            break
        except StopIteration:
            current.rmdir()
            current = current.parent


def delete_bundle(bundle_dir: Path, *, media_root: Path = MEDIA_ROOT, static_root: Path = ROOT / "static") -> None:
    bundle_dir = bundle_dir.resolve()
    media_root = media_root.resolve()
    static_root = static_root.resolve()

    if media_root not in bundle_dir.parents:
        raise ValueError(f"Refusing to delete bundle outside media root: {bundle_dir}")
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        raise FileNotFoundError(bundle_dir)

    front_matter, _ = read_markdown_file(bundle_dir / "index.md")
    original_path = original_file_for_bundle(bundle_dir)
    old_url = _clean_image_meta_text(front_matter.get("old_url"))

    if old_url and original_path is not None:
        try:
            link_path = static_path_for_old_url(old_url, static_root=static_root)
        except ValueError:
            link_path = None
        if link_path is not None and link_path.is_symlink():
            expected_target = relative_symlink_target(link_path, original_path)
            if os.readlink(link_path) == expected_target:
                link_path.unlink()
                _prune_empty_parents(link_path.parent, static_root)

    shutil.rmtree(bundle_dir)
    _prune_empty_parents(bundle_dir.parent, media_root)


def create_bundle_from_media(
    *,
    source_path: Path,
    source_filename: str,
    title: str | None,
    date_value: str | None,
    media_root: Path = MEDIA_ROOT,
    tags: list[str] | None = None,
    galleries: list[str] | None = None,
) -> Path:
    prepare_section_root(media_root)

    probe = probe_media_file(source_path)
    timestamp = parse_timestamp(date_value) or datetime.now()

    period_folder = timestamp.strftime("%Y-%m")
    bundle_parent = media_root / period_folder
    base_title = title or title_from_filename(source_filename)
    bundle_dir = ensure_unique_bundle_dir(bundle_parent, Path(source_filename).stem or "image")
    suffix = Path(source_filename).suffix.lower()
    original_name = f"{MEDIA_RESOURCE_BASENAME}{suffix}" if suffix else MEDIA_RESOURCE_BASENAME

    bundle_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, bundle_dir / original_name)

    front_matter = build_front_matter(
        title=base_title,
        date=timestamp.strftime("%Y-%m-%d"),
        lastmod=format_timestamp(datetime.now()),
        alt=None,
        caption=None,
        description=None,
        tags=tags or [],
        galleries=galleries or [],
        credit=None,
        location=None,
        original_filename=source_filename,
        filesize=int(probe["filesize"]) if probe.get("filesize") else None,
        sha256=str(probe.get("sha256") or "").strip() or None,
        uploaded_at_gmt=None,
        uploaded_at_local=None,
        modified_at_gmt=None,
        modified_at_local=None,
        wordpress_id=None,
        image_meta=None,
        old_url=None,
    )
    write_markdown_file(bundle_dir / "index.md", front_matter)
    return bundle_dir


def create_bundle_from_image(
    *,
    source_path: Path,
    source_filename: str,
    title: str | None,
    date_value: str | None,
    media_root: Path = MEDIA_ROOT,
    tags: list[str] | None = None,
    galleries: list[str] | None = None,
) -> Path:
    return create_bundle_from_media(
        source_path=source_path,
        source_filename=source_filename,
        title=title,
        date_value=date_value,
        media_root=media_root,
        tags=tags,
        galleries=galleries,
    )


def static_path_for_old_url(old_url: str, *, static_root: Path = ROOT / "static") -> Path:
    parsed = urlparse(str(old_url or "").strip())
    if not parsed.path:
        raise ValueError(f"Missing path in old URL: {old_url!r}")
    clean_path = parsed.path.lstrip("/")
    if not clean_path:
        raise ValueError(f"Invalid old URL path: {old_url!r}")
    return static_root / Path(clean_path)


def relative_symlink_target(link_path: Path, target_path: Path) -> str:
    return os.path.relpath(target_path, start=link_path.parent)


def build_duplicate_groups(items: list[dict[str, Any]]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for item in items:
        sha256 = item.get("sha256")
        if sha256:
            groups[str(sha256)].append(str(item.get("media_path")))
    return {key: value for key, value in groups.items() if len(value) > 1}


def load_wordpress_library() -> dict[str, Any]:
    return json.loads(WORDPRESS_LIBRARY.read_text(encoding="utf-8"))


def reset_media_root(root: Path = MEDIA_ROOT) -> None:
    if root.exists():
        shutil.rmtree(root)
