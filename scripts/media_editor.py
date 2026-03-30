#!/usr/bin/env python3

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import shutil
import tempfile
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from media_bundles import (
    MEDIA_ROOT,
    ROOT,
    build_duplicate_groups,
    create_bundle_from_media,
    delete_bundle,
    gallery_media_file_path,
    gallery_thumbnail_path,
    list_media_items,
    load_bundle,
    media_kind_for_mime,
    normalize_comma_list,
    original_file_for_bundle,
    save_bundle_metadata,
    title_from_filename,
)

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - available in this workspace
    Image = None
    ImageOps = None


UI_ROOT = Path(__file__).with_name("media_editor_ui")
THUMBNAIL_CACHE: dict[tuple[str, int, int], tuple[bytes, str]] = {}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local FSV media editor.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a quick media-library summary and exit instead of starting the server.",
    )
    return parser.parse_args()


def resolve_bundle_dir(media_path: str) -> Path:
    candidate = (MEDIA_ROOT / Path(media_path)).resolve()
    media_root = MEDIA_ROOT.resolve()
    if media_root not in candidate.parents and candidate != media_root:
        raise ValueError(f"Invalid media path: {media_path}")
    if not candidate.exists() or not candidate.is_dir():
        raise FileNotFoundError(candidate)
    return candidate


def item_matches_query(item: dict[str, object], query: str) -> bool:
    if not query:
        return True
    haystack = " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("alt") or ""),
            str(item.get("caption") or ""),
            str(item.get("description") or ""),
            ", ".join(item.get("tags") or []),
            ", ".join(item.get("galleries") or []),
            str(item.get("original_filename") or ""),
            str(item.get("old_url") or ""),
            str(item.get("media_path") or ""),
        ]
    ).lower()
    return query.lower() in haystack


def collect_items(query: str = "", missing_alt: bool = False, duplicates_only: bool = False) -> list[dict[str, object]]:
    items = list_media_items(MEDIA_ROOT)
    duplicate_groups = build_duplicate_groups(items)
    duplicate_sizes = {
        media_path: len(paths)
        for paths in duplicate_groups.values()
        for media_path in paths
    }

    enriched: list[dict[str, object]] = []
    for item in items:
        item = dict(item)
        item["duplicate_group_size"] = duplicate_sizes.get(str(item["media_path"]), 1)
        item["thumb_url"] = gallery_thumbnail_path(MEDIA_ROOT / item["media_path"])
        item["image_url"] = gallery_media_file_path(MEDIA_ROOT / item["media_path"])

        if missing_alt and not item.get("missing_alt"):
            continue
        if duplicates_only and int(item["duplicate_group_size"]) < 2:
            continue
        if not item_matches_query(item, query):
            continue
        enriched.append(item)

    enriched.sort(key=lambda item: (item.get("date") or "", item.get("title") or ""), reverse=True)
    return enriched


def placeholder_thumbnail_bytes(original_path: Path, size: int, kind: str) -> tuple[bytes, str]:
    label = "VIDEO" if kind == "video" else "DATEI"
    extension = original_path.suffix.lstrip(".").upper() or kind.upper()
    svg = f"""
<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 400 300">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#d7e0ef" />
      <stop offset="100%" stop-color="#eef1e7" />
    </linearGradient>
  </defs>
  <rect width="400" height="300" rx="28" fill="url(#bg)" />
  <circle cx="200" cy="118" r="46" fill="rgba(36,77,122,0.18)" />
  <polygon points="188,94 188,142 226,118" fill="#244d7a" />
  <text x="200" y="205" text-anchor="middle" font-family="IBM Plex Sans, Segoe UI, sans-serif"
        font-size="28" font-weight="700" fill="#1e281d">{html.escape(label)}</text>
  <text x="200" y="236" text-anchor="middle" font-family="IBM Plex Sans, Segoe UI, sans-serif"
        font-size="18" fill="#566553">{html.escape(extension)}</text>
</svg>
""".strip()
    return svg.encode("utf-8"), "image/svg+xml"


def thumbnail_bytes(bundle_dir: Path, size: int) -> tuple[bytes, str]:
    original_path = original_file_for_bundle(bundle_dir)
    if original_path is None:
        raise FileNotFoundError(f"Missing original file in {bundle_dir}")

    original_mime = mimetypes.guess_type(original_path.name)[0] or "application/octet-stream"
    media_kind = media_kind_for_mime(original_mime)
    if media_kind != "image":
        return placeholder_thumbnail_bytes(original_path, size, media_kind)

    if Image is None or ImageOps is None:
        return original_path.read_bytes(), original_mime

    stat = original_path.stat()
    cache_key = (str(original_path), size, stat.st_mtime_ns)
    cached = THUMBNAIL_CACHE.get(cache_key)
    if cached is not None:
        return cached

    with Image.open(original_path) as image:
        image = ImageOps.exif_transpose(image)
        image.thumbnail((size, size))

        mime_type = "image/jpeg"
        save_kwargs = {"format": "JPEG", "quality": 82}
        if image.mode in {"RGBA", "LA"}:
            mime_type = "image/png"
            save_kwargs = {"format": "PNG"}
        else:
            image = image.convert("RGB")

        buffer = BytesIO()
        image.save(buffer, **save_kwargs)
        payload = buffer.getvalue()

    THUMBNAIL_CACHE[cache_key] = (payload, mime_type)
    return payload, mime_type


def parse_multipart_files(content_type: str, payload: bytes) -> list[tuple[str, bytes]]:
    message = BytesParser(policy=email_policy).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + payload
    )

    files: list[tuple[str, bytes]] = []
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        filename = part.get_filename()
        field_name = part.get_param("name", header="content-disposition")
        if field_name != "files" or not filename:
            continue
        files.append((filename, part.get_payload(decode=True) or b""))
    return files


class MediaEditorHandler(BaseHTTPRequestHandler):
    server_version = "FSVMediaEditor/1.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path == "/":
            self._serve_static("index.html", "text/html; charset=utf-8")
            return
        if parsed.path == "/styles.css":
            self._serve_static("styles.css", "text/css; charset=utf-8")
            return
        if parsed.path == "/app.js":
            self._serve_static("app.js", "application/javascript; charset=utf-8")
            return
        if parsed.path == "/api/images":
            self._handle_list_images(parsed)
            return
        if parsed.path.startswith("/thumb/"):
            self._handle_thumbnail(parsed)
            return
        if parsed.path.startswith("/media-file/"):
            self._handle_media_file(parsed)
            return
        if parsed.path.startswith("/original/"):
            self._handle_media_file(parsed)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path == "/api/item/save":
            self._handle_save_item()
            return
        if parsed.path == "/api/item/delete":
            self._handle_delete_item()
            return
        if parsed.path == "/api/upload":
            self._handle_upload()
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _serve_static(self, name: str, content_type: str) -> None:
        payload = (UI_ROOT / name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, payload: str, status: HTTPStatus) -> None:
        body = payload.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, payload: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _send_file_path(self, path: Path, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as handle:
            shutil.copyfileobj(handle, self.wfile)

    def _handle_list_images(self, parsed: object) -> None:
        query = parse_qs(parsed.query)
        items = collect_items(
            query=str(query.get("q", [""])[0]),
            missing_alt=query.get("missing_alt", ["0"])[0] == "1",
            duplicates_only=query.get("duplicates", ["0"])[0] == "1",
        )
        self._send_json({"items": items})

    def _handle_thumbnail(self, parsed: object) -> None:
        media_path = unquote(parsed.path[len("/thumb/") :])
        query = parse_qs(parsed.query)
        size = int(query.get("size", ["320"])[0] or 320)
        bundle_dir = resolve_bundle_dir(media_path)
        payload, content_type = thumbnail_bytes(bundle_dir, size)
        self._send_file(payload, content_type)

    def _handle_media_file(self, parsed: object) -> None:
        if parsed.path.startswith("/media-file/"):
            media_path = unquote(parsed.path[len("/media-file/") :])
        else:
            media_path = unquote(parsed.path[len("/original/") :])
        bundle_dir = resolve_bundle_dir(media_path)
        original_path = original_file_for_bundle(bundle_dir)
        if original_path is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(original_path.name)[0] or "application/octet-stream"
        self._send_file_path(original_path, content_type)

    def _handle_save_item(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            media_path = str(payload.get("path") or "")
            bundle_dir = resolve_bundle_dir(media_path)
            current = load_bundle(bundle_dir)

            updated_title = str(payload.get("title") or "").strip() or title_from_filename(
                str(current.get("original_filename") or current.get("resource_filename") or "image")
            )
            metadata_updates = {
                "title": updated_title,
                "alt": str(payload.get("alt") or ""),
                "description": str(payload.get("description") or ""),
                "tags": normalize_comma_list(payload.get("tags")),
            }

            save_bundle_metadata(bundle_dir, metadata_updates)
            item = load_bundle(bundle_dir)
            duplicates = build_duplicate_groups(collect_items())
            item["duplicate_group_size"] = next(
                (len(paths) for paths in duplicates.values() if item["media_path"] in paths),
                1,
            )
            self._send_json({"item": item})
        except Exception as error:  # pragma: no cover - exercised manually
            self._send_text(str(error), HTTPStatus.BAD_REQUEST)

    def _handle_delete_item(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            media_path = str(payload.get("path") or "")
            bundle_dir = resolve_bundle_dir(media_path)
            delete_bundle(bundle_dir)
            self._send_json({"deleted": media_path})
        except Exception as error:  # pragma: no cover - exercised manually
            self._send_text(str(error), HTTPStatus.BAD_REQUEST)

    def _handle_upload(self) -> None:
        try:
            content_type = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", "0"))
            files = parse_multipart_files(content_type, self.rfile.read(length))
            created: list[str] = []

            for filename, file_bytes in files:
                suffix = Path(filename).suffix
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_handle:
                    temp_handle.write(file_bytes)
                    temp_path = Path(temp_handle.name)

                try:
                    bundle_dir = create_bundle_from_media(
                        source_path=temp_path,
                        source_filename=filename,
                        title=None,
                        date_value=None,
                    )
                    created.append(bundle_dir.relative_to(MEDIA_ROOT).as_posix())
                finally:
                    temp_path.unlink(missing_ok=True)

            self._send_json({"created": created}, status=HTTPStatus.CREATED)
        except Exception as error:  # pragma: no cover - exercised manually
            self._send_text(str(error), HTTPStatus.BAD_REQUEST)


def main() -> int:
    args = parse_args()

    if args.summary:
        items = list_media_items(MEDIA_ROOT)
        duplicates = build_duplicate_groups(items)
        print(
            json.dumps(
                {
                    "items": len(items),
                    "duplicate_groups": len(duplicates),
                    "media_root": MEDIA_ROOT.relative_to(ROOT).as_posix(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    server = ThreadingHTTPServer((args.host, args.port), MediaEditorHandler)
    print(f"Media editor listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
