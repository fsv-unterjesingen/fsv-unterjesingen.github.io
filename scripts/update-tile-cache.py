#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import pathlib
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib


ROOT = pathlib.Path(__file__).resolve().parents[1]
CACHE_ROOT = ROOT / "static" / "tiles"
OPENFREEMAP_CACHE = CACHE_ROOT / "openfreemap"
OPENAIP_CACHE = CACHE_ROOT / "openaip"
MANIFEST_PATH = CACHE_ROOT / "index.json"
STYLE_CACHE = CACHE_ROOT / "style"
SPRITES_CACHE = STYLE_CACHE / "sprites"
FONTS_CACHE = STYLE_CACHE / "fonts"
TILEJSON_CACHE = STYLE_CACHE / "tilejson"

STYLE_URL = os.environ.get(
    "OPENFREEMAP_STYLE_URL",
    "https://styles.trailsta.sh/openmaptiles-osm.json",
)

OPENAIP_TILE_URL = "https://api.tiles.openaip.net/api/data/openaip/{z}/{x}/{y}.pbf"

# Default view bounds (west, south, east, north)
BOUNDS = (8.2, 48.0, 9.7, 49.0)

# Cache zooms: default zoom is 9, so include 9 and one higher level.
ZOOMS = (9,)

# Target max cache size (bytes) across tiles and style assets.
MAX_CACHE_BYTES = 10 * 1024 * 1024
GLYPH_RANGES = tuple(
    r.strip()
    for r in os.environ.get("GLYPH_RANGES", "0-255,256-511").split(",")
    if r.strip()
)
EXTRA_FONTSTACKS = tuple(
    f.strip()
    for f in os.environ.get(
        "EXTRA_FONTSTACKS",
        "Open Sans Regular,Arial Unicode MS Regular",
    ).split(";")
    if f.strip()
)


def log(msg: str) -> None:
    print(msg, flush=True)


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int(
        (1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi)
        / 2.0
        * n
    )
    x = max(0, min(n - 1, x))
    y = max(0, min(n - 1, y))
    return x, y


def tile_range_for_bounds(bounds: tuple[float, float, float, float], zoom: int) -> list[tuple[int, int, int]]:
    west, south, east, north = bounds
    n = 2**zoom
    x_min, _ = lonlat_to_tile(west, north, zoom)
    x_max, _ = lonlat_to_tile(east, north, zoom)
    _, y_min = lonlat_to_tile(west, north, zoom)
    _, y_max = lonlat_to_tile(west, south, zoom)
    x_min = max(0, min(n - 1, x_min))
    x_max = max(0, min(n - 1, x_max))
    y_min = max(0, min(n - 1, y_min))
    y_max = max(0, min(n - 1, y_max))

    tiles: list[tuple[int, int, int]] = []
    for x in range(min(x_min, x_max), max(x_min, x_max) + 1):
        for y in range(min(y_min, y_max), max(y_min, y_max) + 1):
            tiles.append((zoom, x, y))
    return tiles


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "tile-cache-updater/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "tile-cache-updater/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def discover_openfreemap_tile_url() -> str:
    override = os.environ.get("OPENFREEMAP_TILE_URL")
    if override:
        return override

    log(f"Fetching style JSON from {STYLE_URL} ...")
    style = fetch_json(STYLE_URL)
    sources = style.get("sources", {})
    for source in sources.values():
        tiles = source.get("tiles") if isinstance(source, dict) else None
        if not tiles:
            continue
        for tile_url in tiles:
            if ".pbf" in tile_url:
                log(f"Using tiles: {tile_url}")
                return tile_url

    for source in sources.values():
        tilejson_url = source.get("url") if isinstance(source, dict) else None
        if not tilejson_url:
            continue
        try:
            tilejson = fetch_json(tilejson_url)
        except Exception:
            continue
        tiles = tilejson.get("tiles") if isinstance(tilejson, dict) else None
        if not tiles:
            continue
        for tile_url in tiles:
            if ".pbf" in tile_url:
                log(f"Using tiles from TileJSON: {tile_url}")
                return tile_url

    raise RuntimeError(
        "Could not find PBF tile URL in style JSON. Set OPENFREEMAP_TILE_URL to override."
    )


def style_basename() -> str:
    override = os.environ.get("LOCAL_STYLE_NAME")
    if override:
        return override
    parsed = urlparse(STYLE_URL)
    name = os.path.basename(parsed.path)
    return name or "openmaptiles-osm.json"


def read_openaip_api_key() -> str:
    env_key = os.environ.get("OPENAIP_API_KEY")
    if env_key:
        return env_key
    config_path = ROOT / "hugo.toml"
    if not config_path.exists():
        return ""
    data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    params = data.get("params", {})
    return params.get("openaipApiKey", "")


def cache_size_bytes(path: pathlib.Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix not in {".pbf", ".png", ".json"}:
            continue
        try:
            total += p.stat().st_size
        except FileNotFoundError:
            continue
    return total


def ensure_parent(path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def download_tile(url: str, dest: pathlib.Path) -> int:
    ensure_parent(dest)
    req = urllib.request.Request(url, headers={"User-Agent": "tile-cache-updater/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    dest.write_bytes(data)
    return len(data)


def cache_style_assets() -> dict:
    log(f"Fetching style JSON from {STYLE_URL} ...")
    style = fetch_json(STYLE_URL)
    style_name = style_basename()
    style_stem = style_name.rsplit(".", 1)[0]

    sprite_base = style.get("sprite")
    if sprite_base:
        for suffix in (".json", ".png", "@2x.json", "@2x.png"):
            sprite_url = f"{sprite_base}{suffix}"
            try:
                data = fetch_bytes(sprite_url)
            except Exception:
                continue
            dest = SPRITES_CACHE / f"{style_stem}{suffix}"
            ensure_parent(dest)
            dest.write_bytes(data)

        style["sprite"] = f"/tiles/style/sprites/{style_stem}"

    glyphs = style.get("glyphs")
    if glyphs:
        font_stacks: set[str] = set()
        for layer in style.get("layers", []):
            layout = layer.get("layout", {}) if isinstance(layer, dict) else {}
            text_fonts = layout.get("text-font")
            if isinstance(text_fonts, list):
                for font in text_fonts:
                    if isinstance(font, str):
                        font_stacks.add(font)
                joined = ",".join([f for f in text_fonts if isinstance(f, str)])
                if joined:
                    font_stacks.add(joined)
            elif isinstance(text_fonts, str):
                font_stacks.add(text_fonts)

        for extra_stack in EXTRA_FONTSTACKS:
            font_stacks.add(extra_stack)

        for font in sorted(font_stacks):
            encoded_font = quote(font, safe=",")
            for glyph_range in GLYPH_RANGES:
                glyph_url = (
                    glyphs.replace("{fontstack}", encoded_font).replace("{range}", glyph_range)
                )
                try:
                    data = fetch_bytes(glyph_url)
                except Exception:
                    continue
                dest = FONTS_CACHE / font / f"{glyph_range}.pbf"
                ensure_parent(dest)
                dest.write_bytes(data)

        style["glyphs"] = "/tiles/style/fonts/{fontstack}/{range}.pbf"

    sources = style.get("sources", {})
    if isinstance(sources, dict):
        for source_name, source in sources.items():
            if not isinstance(source, dict):
                continue
            tilejson_url = source.get("url")
            if not tilejson_url:
                continue
            try:
                data = fetch_bytes(tilejson_url)
            except Exception:
                continue
            safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in source_name)
            dest = TILEJSON_CACHE / f"{safe_name}.json"
            ensure_parent(dest)
            dest.write_bytes(data)
            source["url"] = f"/tiles/style/tilejson/{safe_name}.json"

    STYLE_CACHE.mkdir(parents=True, exist_ok=True)
    (STYLE_CACHE / style_name).write_text(json.dumps(style, indent=2), encoding="utf-8")
    return style


def cache_tiles(name: str, template: str, dest_root: pathlib.Path, tiles: list[tuple[int, int, int]]) -> None:
    log(f"Caching {name} tiles into {dest_root} ...")
    for z, x, y in tiles:
        url = template.format(z=z, x=x, y=y)
        dest = dest_root / str(z) / str(x) / f"{y}.pbf"
        try:
            download_tile(url, dest)
        except urllib.error.HTTPError as e:
            log(f"Skip {url} (HTTP {e.code})")
            continue
        except urllib.error.URLError as e:
            log(f"Skip {url} ({e.reason})")
            continue

        total_size = cache_size_bytes(CACHE_ROOT)
        if total_size > MAX_CACHE_BYTES:
            log(
                f"Cache size limit exceeded after {url} "
                f"({total_size / 1024 / 1024:.2f} MiB). Removing tile and stopping."
            )
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            break
        time.sleep(0.05)


def build_manifest() -> dict:
    def collect_tiles(root: pathlib.Path) -> list[str]:
        tiles: list[str] = []
        if not root.exists():
            return tiles
        for p in root.rglob("*.pbf"):
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            parts = rel.parts
            if len(parts) != 3:
                continue
            z, x, yfile = parts
            if not yfile.endswith(".pbf"):
                continue
            y = yfile[:-4]
            if not (z.isdigit() and x.isdigit() and y.isdigit()):
                continue
            tiles.append(f"{z}/{x}/{y}")
        return sorted(set(tiles))

    return {
        "bounds": {"west": BOUNDS[0], "south": BOUNDS[1], "east": BOUNDS[2], "north": BOUNDS[3]},
        "zooms": list(ZOOMS),
        "openfreemap": collect_tiles(OPENFREEMAP_CACHE),
        "openaip": collect_tiles(OPENAIP_CACHE),
        "generated_at": int(time.time()),
    }


def main() -> int:
    tiles: list[tuple[int, int, int]] = []
    for zoom in ZOOMS:
        tiles.extend(tile_range_for_bounds(BOUNDS, zoom))

    log(f"Tiles to cache: {len(tiles)} total across zooms {ZOOMS}.")

    cache_style_assets()

    openfreemap_template = discover_openfreemap_tile_url()

    openaip_key = read_openaip_api_key()
    openaip_template = OPENAIP_TILE_URL
    if openaip_key:
        openaip_template = f"{OPENAIP_TILE_URL}?apiKey={openaip_key}"
    else:
        log("Warning: OpenAIP API key not found. Requests may be rate limited.")

    cache_tiles("OpenFreeMap", openfreemap_template, OPENFREEMAP_CACHE, tiles)
    cache_tiles("OpenAIP", openaip_template, OPENAIP_CACHE, tiles)
    MANIFEST_PATH.write_text(json.dumps(build_manifest(), indent=2), encoding="utf-8")
    log(f"Final cache size: {cache_size_bytes(CACHE_ROOT) / 1024 / 1024:.2f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
