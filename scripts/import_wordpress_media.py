#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

try:
    from PIL import Image
except ImportError:  # pragma: no cover - Pillow is available in this workspace.
    Image = None


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SQL_DUMP = ROOT / "old-site-backup" / "wp-db-backup.sql"
DEFAULT_TAR_BACKUP = ROOT / "old-site-backup" / "wp-backup.tar.gz"
DEFAULT_ASSET_ROOT = ROOT / "assets" / "images" / "wordpress-media" / "originals"
DEFAULT_DATA_FILE = ROOT / "data" / "wordpress_media" / "library.json"
DEFAULT_MANIFEST_FILE = ROOT / "artifacts" / "wordpress-media-manifest.json"
UPLOADS_MARKER = "wp-content/uploads/"
SUPPORTED_MIME_PREFIXES = ("image/", "video/")
META_KEYS = {
    "_wp_attached_file",
    "_wp_attachment_metadata",
    "_wp_attachment_image_alt",
}
SQL_NULL = object()


@dataclass
class AttachmentPost:
    id: int
    post_date: str
    post_date_gmt: str
    post_content: str
    post_title: str
    post_excerpt: str
    post_status: str
    post_name: str
    post_modified: str
    post_modified_gmt: str
    post_parent: int
    guid: str
    menu_order: int
    post_mime_type: str


@dataclass
class PlannedImport:
    attachment_id: int
    post: AttachmentPost
    alt: str | None
    attached_file: str
    metadata: dict[str, Any] | None
    preferred_source: str
    safe_source: PurePosixPath
    destination: Path
    resource_path: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Import WordPress media attachments into Hugo assets and emit a "
            "matching Hugo data file with the preserved metadata."
        )
    )
    parser.add_argument("--sql-dump", type=Path, default=DEFAULT_SQL_DUMP)
    parser.add_argument("--tar-backup", type=Path, default=DEFAULT_TAR_BACKUP)
    parser.add_argument("--asset-root", type=Path, default=DEFAULT_ASSET_ROOT)
    parser.add_argument("--data-file", type=Path, default=DEFAULT_DATA_FILE)
    parser.add_argument("--manifest-file", type=Path, default=DEFAULT_MANIFEST_FILE)
    return parser.parse_args()


def decode_mysql_string(value: str) -> str:
    result: list[str] = []
    i = 0
    while i < len(value):
        char = value[i]
        if char != "\\":
            result.append(char)
            i += 1
            continue

        i += 1
        if i >= len(value):
            result.append("\\")
            break

        escaped = value[i]
        result.append(
            {
                "0": "\0",
                "b": "\b",
                "n": "\n",
                "r": "\r",
                "t": "\t",
                "Z": "\x1a",
                "\\": "\\",
                "'": "'",
                '"': '"',
            }.get(escaped, escaped)
        )
        i += 1

    return "".join(result)


def parse_sql_value(line: str, index: int) -> tuple[Any, int]:
    if line[index] == "'":
        index += 1
        chunks: list[str] = []
        while index < len(line):
            char = line[index]
            if char == "\\":
                if index + 1 >= len(line):
                    chunks.append("\\")
                    index += 1
                    continue
                chunks.append("\\" + line[index + 1])
                index += 2
                continue
            if char == "'":
                index += 1
                return decode_mysql_string("".join(chunks)), index
            chunks.append(char)
            index += 1
        raise ValueError("Unterminated SQL string literal")

    if line.startswith("NULL", index):
        return SQL_NULL, index + 4

    start = index
    while index < len(line) and line[index] not in ",)":
        index += 1
    raw = line[start:index]
    if raw == "":
        raise ValueError("Encountered empty unquoted SQL value")
    return raw, index


def iter_insert_rows(sql_dump: Path, table: str) -> Iterator[list[Any]]:
    prefix = f"INSERT INTO `{table}` VALUES "
    with sql_dump.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.startswith(prefix):
                continue

            payload = line[len(prefix) :].rstrip()
            if payload.endswith(";"):
                payload = payload[:-1]

            index = 0
            length = len(payload)
            while index < length:
                if payload[index] != "(":
                    raise ValueError(f"Expected '(' at offset {index} in {table} INSERT")
                index += 1

                row: list[Any] = []
                while True:
                    value, index = parse_sql_value(payload, index)
                    row.append(value)

                    if payload[index] == ",":
                        index += 1
                        continue
                    if payload[index] == ")":
                        index += 1
                        break
                    raise ValueError(
                        f"Expected ',' or ')' at offset {index} in {table} INSERT"
                    )

                yield row

                if index >= length:
                    break
                if payload[index] != ",":
                    raise ValueError(f"Expected row separator at offset {index}")
                index += 1


class PHPSerializedParser:
    def __init__(self, payload: str) -> None:
        self.payload = payload.encode("utf-8")
        self.index = 0

    def parse(self) -> Any:
        value = self._parse_value()
        if self.index != len(self.payload):
            raise ValueError("Unexpected trailing bytes in serialized payload")
        return value

    def _read_until(self, delimiter: bytes) -> bytes:
        end = self.payload.index(delimiter, self.index)
        chunk = self.payload[self.index : end]
        self.index = end + len(delimiter)
        return chunk

    def _parse_length(self) -> int:
        return int(self._read_until(b":").decode("ascii"))

    def _parse_value(self) -> Any:
        type_code = chr(self.payload[self.index])
        self.index += 1

        if type_code == "N":
            if self.payload[self.index : self.index + 1] != b";":
                raise ValueError("Malformed NULL value")
            self.index += 1
            return None

        if self.payload[self.index : self.index + 1] != b":":
            raise ValueError(f"Missing ':' after type code {type_code!r}")
        self.index += 1

        if type_code == "b":
            value = self._read_until(b";")
            return value == b"1"

        if type_code == "i":
            return int(self._read_until(b";").decode("ascii"))

        if type_code == "d":
            return float(self._read_until(b";").decode("ascii"))

        if type_code == "s":
            size = self._parse_length()
            if self.payload[self.index : self.index + 1] != b'"':
                raise ValueError("Malformed serialized string header")
            self.index += 1
            raw = self.payload[self.index : self.index + size]
            self.index += size
            if self.payload[self.index : self.index + 2] != b'";':
                raise ValueError("Malformed serialized string terminator")
            self.index += 2
            return raw.decode("utf-8", errors="replace")

        if type_code == "a":
            length = self._parse_length()
            if self.payload[self.index : self.index + 1] != b"{":
                raise ValueError("Malformed serialized array header")
            self.index += 1
            items: list[tuple[Any, Any]] = []
            for _ in range(length):
                key = self._parse_value()
                value = self._parse_value()
                items.append((key, value))
            if self.payload[self.index : self.index + 1] != b"}":
                raise ValueError("Malformed serialized array terminator")
            self.index += 1
            return normalize_php_array(items)

        raise ValueError(f"Unsupported serialized type code: {type_code!r}")


def normalize_php_array(items: list[tuple[Any, Any]]) -> Any:
    if all(isinstance(key, int) for key, _ in items):
        ordered = sorted(items, key=lambda item: item[0])
        expected = list(range(len(ordered)))
        if [key for key, _ in ordered] == expected:
            return [value for _, value in ordered]
    return {str(key): value for key, value in items}


def maybe_parse_php_serialized(value: str) -> Any:
    if not value:
        return None
    if value[0] not in {"a", "b", "d", "i", "N", "s"} or ":" not in value[:4]:
        return None
    try:
        return PHPSerializedParser(value).parse()
    except Exception:
        return None


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def media_kind_for_mime(mime_type: str | None) -> str | None:
    if not mime_type:
        return None
    for prefix in SUPPORTED_MIME_PREFIXES:
        if mime_type.startswith(prefix):
            return prefix[:-1]
    return None


def load_attachment_posts(sql_dump: Path) -> dict[int, AttachmentPost]:
    attachments: dict[int, AttachmentPost] = {}
    for row in iter_insert_rows(sql_dump, "wp_posts"):
        if len(row) != 23:
            raise ValueError(f"Unexpected wp_posts column count: {len(row)}")

        post_type = row[20]
        post_mime_type = row[21]
        if post_type != "attachment" or not isinstance(post_mime_type, str):
            continue
        if media_kind_for_mime(post_mime_type) is None:
            continue

        post = AttachmentPost(
            id=int(row[0]),
            post_date=str(row[2]),
            post_date_gmt=str(row[3]),
            post_content=str(row[4]),
            post_title=str(row[5]),
            post_excerpt=str(row[6]),
            post_status=str(row[7]),
            post_name=str(row[11]),
            post_modified=str(row[14]),
            post_modified_gmt=str(row[15]),
            post_parent=int(row[17]),
            guid=str(row[18]),
            menu_order=int(row[19]),
            post_mime_type=post_mime_type,
        )
        attachments[post.id] = post

    return attachments


def load_attachment_meta(sql_dump: Path, attachment_ids: set[int]) -> dict[int, dict[str, Any]]:
    meta_by_post_id: dict[int, dict[str, Any]] = defaultdict(dict)
    for row in iter_insert_rows(sql_dump, "wp_postmeta"):
        if len(row) != 4:
            raise ValueError(f"Unexpected wp_postmeta column count: {len(row)}")

        post_id = int(row[1])
        meta_key = row[2]
        if post_id not in attachment_ids or meta_key not in META_KEYS:
            continue

        meta_by_post_id[post_id][meta_key] = None if row[3] is SQL_NULL else row[3]

    return meta_by_post_id


def safe_relative_upload_path(relative_path: str) -> PurePosixPath:
    candidate = PurePosixPath(relative_path)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise ValueError(f"Unsafe upload path: {relative_path}")
    return candidate


def candidate_relative_paths(member_name: str) -> list[str]:
    marker_index = member_name.find(UPLOADS_MARKER)
    if marker_index < 0:
        return []

    relative_path = member_name[marker_index + len(UPLOADS_MARKER) :]
    candidates = [relative_path]
    try:
        repaired = relative_path.encode("latin-1").decode("utf-8")
    except UnicodeError:
        repaired = None
    if repaired and repaired != relative_path:
        candidates.append(repaired)
    return candidates


def probe_media(path: Path) -> dict[str, Any]:
    details = {
        "filesize": path.stat().st_size,
        "width": None,
        "height": None,
        "sha256": sha256_file(path),
    }
    if Image is None:
        return details
    try:
        with Image.open(path) as image:
            details["width"], details["height"] = image.size
    except Exception:
        pass
    return details


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_member(archive: tarfile.TarFile, member: tarfile.TarInfo, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    extracted = archive.extractfile(member)
    if extracted is None:
        raise FileNotFoundError(member.name)
    with destination.open("wb") as output:
        shutil.copyfileobj(extracted, output)


def prepare_output_paths(asset_root: Path, data_file: Path, manifest_file: Path) -> None:
    if asset_root.exists():
        shutil.rmtree(asset_root)
    asset_root.mkdir(parents=True, exist_ok=True)
    data_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.parent.mkdir(parents=True, exist_ok=True)


def get_preferred_source_path(attached_file: str, metadata: dict[str, Any] | None) -> str:
    if metadata and isinstance(metadata.get("original_image"), str):
        base = PurePosixPath(attached_file)
        return str(base.parent / metadata["original_image"])
    return attached_file


def build_generated_sizes(metadata: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not metadata:
        return []
    sizes = metadata.get("sizes")
    if not isinstance(sizes, dict):
        return []
    results: list[dict[str, Any]] = []
    for name, details in sorted(sizes.items()):
        if not isinstance(details, dict):
            continue
        item = {"name": name}
        for key in ("file", "width", "height", "mime-type", "filesize"):
            if key in details:
                item[key.replace("-", "_")] = details[key]
        results.append(item)
    return results


def build_planned_imports(
    *,
    attachments: dict[int, AttachmentPost],
    attachment_meta: dict[int, dict[str, Any]],
    asset_root: Path,
) -> tuple[dict[str, PlannedImport], list[int], list[int]]:
    planned_by_source: dict[str, PlannedImport] = {}
    missing_attached_file_meta: list[int] = []
    missing_attachment_metadata: list[int] = []

    for attachment_id in sorted(attachments):
        post = attachments[attachment_id]
        meta = attachment_meta.get(attachment_id, {})
        attached_file = meta.get("_wp_attached_file")
        if not attached_file or not isinstance(attached_file, str):
            missing_attached_file_meta.append(attachment_id)
            continue

        metadata = maybe_parse_php_serialized(meta.get("_wp_attachment_metadata", "") or "")
        if metadata is None:
            missing_attachment_metadata.append(attachment_id)
        if not isinstance(metadata, dict):
            metadata = None

        preferred_source = get_preferred_source_path(attached_file, metadata)
        safe_source = safe_relative_upload_path(preferred_source)
        resource_path = str(
            Path("images") / "wordpress-media" / "originals" / Path(*safe_source.parts)
        ).replace("\\", "/")

        planned_by_source[preferred_source] = PlannedImport(
            attachment_id=attachment_id,
            post=post,
            alt=normalize_text(meta.get("_wp_attachment_image_alt")),
            attached_file=attached_file,
            metadata=metadata,
            preferred_source=preferred_source,
            safe_source=safe_source,
            destination=asset_root / Path(*safe_source.parts),
            resource_path=resource_path,
        )

    return planned_by_source, missing_attached_file_meta, missing_attachment_metadata


def build_media_record(planned: PlannedImport, probed: dict[str, Any]) -> dict[str, Any]:
    metadata = planned.metadata
    image_meta = metadata.get("image_meta") if metadata else None
    if not isinstance(image_meta, dict):
        image_meta = None

    return {
        "id": planned.attachment_id,
        "media_kind": media_kind_for_mime(planned.post.post_mime_type),
        "title": normalize_text(planned.post.post_title),
        "slug": normalize_text(planned.post.post_name),
        "description": normalize_text(planned.post.post_content),
        "caption": normalize_text(planned.post.post_excerpt),
        "alt": planned.alt,
        "uploaded_at_local": normalize_text(planned.post.post_date),
        "uploaded_at_gmt": normalize_text(planned.post.post_date_gmt),
        "modified_at_local": normalize_text(planned.post.post_modified),
        "modified_at_gmt": normalize_text(planned.post.post_modified_gmt),
        "mime_type": planned.post.post_mime_type,
        "resource_path": planned.resource_path,
        "source_relative_path": planned.preferred_source,
        "original_filename": Path(planned.preferred_source).name,
        "width": probed["width"] or (metadata.get("width") if metadata else None),
        "height": probed["height"] or (metadata.get("height") if metadata else None),
        "filesize": probed["filesize"] or (metadata.get("filesize") if metadata else None),
        "sha256": probed["sha256"],
        "image_meta": image_meta if planned.post.post_mime_type.startswith("image/") else None,
        "wordpress": {
            "status": planned.post.post_status,
            "guid": normalize_text(planned.post.guid),
            "post_parent": planned.post.post_parent,
            "menu_order": planned.post.menu_order,
            "attached_file": planned.attached_file,
            "metadata_file": metadata.get("file") if metadata else None,
        },
    }


def count_duplicate_hash_groups(imported_media: list[dict[str, Any]]) -> dict[str, int]:
    by_hash: dict[str, int] = defaultdict(int)
    for item in imported_media:
        sha256 = item.get("sha256")
        if sha256:
            by_hash[str(sha256)] += 1

    groups = 0
    files = 0
    for count in by_hash.values():
        if count > 1:
            groups += 1
            files += count
    return {"groups": groups, "files": files}


def make_manifest(
    *,
    imported_media: list[dict[str, Any]],
    total_media_attachments: int,
    total_image_attachments: int,
    total_video_attachments: int,
    missing_files: list[dict[str, Any]],
    missing_attached_file_meta: list[int],
    missing_attachment_metadata: list[int],
    mime_counts: Counter[str],
    generated_size_count: int,
    sql_dump: Path,
    tar_backup: Path,
) -> dict[str, Any]:
    duplicate_groups = count_duplicate_hash_groups(imported_media)
    imported_images = [item for item in imported_media if str(item.get("mime_type") or "").startswith("image/")]
    imported_videos = [item for item in imported_media if str(item.get("mime_type") or "").startswith("video/")]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "sql_dump": str(sql_dump.relative_to(ROOT)),
            "tar_backup": str(tar_backup.relative_to(ROOT)),
        },
        "summary": {
            "total_media_attachments": total_media_attachments,
            "total_image_attachments": total_image_attachments,
            "total_video_attachments": total_video_attachments,
            "imported_media": len(imported_media),
            "imported_images": len(imported_images),
            "imported_videos": len(imported_videos),
            "missing_files": len(missing_files),
            "missing_attached_file_meta": len(missing_attached_file_meta),
            "missing_attachment_metadata": len(missing_attachment_metadata),
            "generated_size_variants_discarded": generated_size_count,
            "duplicate_original_groups": duplicate_groups["groups"],
            "duplicate_original_files": duplicate_groups["files"],
            "mime_types": dict(sorted(mime_counts.items())),
        },
        "missing_files": missing_files,
        "missing_attached_file_meta": missing_attached_file_meta,
        "missing_attachment_metadata": missing_attachment_metadata,
    }


def main() -> int:
    args = parse_args()

    attachments = load_attachment_posts(args.sql_dump)
    attachment_meta = load_attachment_meta(args.sql_dump, set(attachments))
    planned_by_source, missing_attached_file_meta, missing_attachment_metadata = (
        build_planned_imports(
            attachments=attachments,
            attachment_meta=attachment_meta,
            asset_root=args.asset_root,
        )
    )
    generated_size_count = sum(
        len(build_generated_sizes(planned.metadata)) for planned in planned_by_source.values()
    )

    prepare_output_paths(args.asset_root, args.data_file, args.manifest_file)

    imported_media: list[dict[str, Any]] = []
    found_sources: set[str] = set()
    mime_counts: Counter[str] = Counter()

    with tarfile.open(args.tar_backup, "r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            planned = None
            matched_source = None
            for relative_path in candidate_relative_paths(member.name):
                planned = planned_by_source.get(relative_path)
                if planned is not None:
                    matched_source = relative_path
                    break
            if planned is None or matched_source is None:
                continue

            copy_member(archive, member, planned.destination)
            probed = probe_media(planned.destination)
            imported_media.append(build_media_record(planned, probed))
            mime_counts[planned.post.post_mime_type] += 1
            found_sources.add(matched_source)

    imported_media.sort(
        key=lambda item: (
            item.get("uploaded_at_local") or "",
            item.get("id") or 0,
        )
    )

    missing_files = [
        {
            "id": planned.attachment_id,
            "attached_file": planned.attached_file,
            "preferred_source": planned.preferred_source,
        }
        for source, planned in sorted(planned_by_source.items())
        if source not in found_sources
    ]

    manifest = make_manifest(
        imported_media=imported_media,
        total_media_attachments=len(attachments),
        total_image_attachments=sum(
            1 for attachment in attachments.values() if attachment.post_mime_type.startswith("image/")
        ),
        total_video_attachments=sum(
            1 for attachment in attachments.values() if attachment.post_mime_type.startswith("video/")
        ),
        missing_files=missing_files,
        missing_attached_file_meta=missing_attached_file_meta,
        missing_attachment_metadata=missing_attachment_metadata,
        mime_counts=mime_counts,
        generated_size_count=generated_size_count,
        sql_dump=args.sql_dump,
        tar_backup=args.tar_backup,
    )

    library = {
        "generated_at": manifest["generated_at"],
        "summary": manifest["summary"],
        "items": imported_media,
        "images": [
            item for item in imported_media if str(item.get("mime_type") or "").startswith("image/")
        ],
        "videos": [
            item for item in imported_media if str(item.get("mime_type") or "").startswith("video/")
        ],
    }

    args.data_file.write_text(
        json.dumps(library, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.manifest_file.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(manifest["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
