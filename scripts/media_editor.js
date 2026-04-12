import { createServer } from "node:http";
import { STATUS_CODES } from "node:http";
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";
import {
  MEDIA_ROOT,
  ROOT,
  buildDuplicateGroups,
  createBundleFromMedia,
  galleryMediaFilePath,
  galleryThumbnailPath,
  inspectMediaFile,
  listMediaItems,
  loadBundle,
  mediaKindForMime,
  normalizeCommaList,
  originalFileForBundle,
  relativeMediaPath,
  saveBundleMetadata,
  titleFromFilename,
  deleteBundle,
} from "./media_bundles.js";
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const UI_ROOT = path.join(SCRIPT_DIR, "media_editor_ui");
const THUMBNAIL_CACHE = new Map();

function parseArgs(argv) {
  const args = { host: "127.0.0.1", port: 4173, summary: false };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--host") {
      args.host = argv[index + 1] ?? args.host;
      index += 1;
      continue;
    }
    if (value === "--port") {
      const parsed = Number.parseInt(argv[index + 1] ?? "", 10);
      if (Number.isFinite(parsed)) {
        args.port = parsed;
      }
      index += 1;
      continue;
    }
    if (value === "--summary") {
      args.summary = true;
    }
  }
  return args;
}

function send(res, status, headers, body) {
  res.writeHead(status, headers);
  res.end(body);
}

function sendJson(res, payload, status = 200) {
  const body = Buffer.from(JSON.stringify(payload), "utf8");
  send(
    res,
    status,
    {
      "Content-Type": "application/json; charset=utf-8",
      "Content-Length": String(body.length),
    },
    body,
  );
}

function sendText(res, payload, status) {
  const body = Buffer.from(payload, "utf8");
  send(
    res,
    status,
    {
      "Content-Type": "text/plain; charset=utf-8",
      "Content-Length": String(body.length),
    },
    body,
  );
}

function sendBuffer(res, payload, contentType) {
  send(
    res,
    200,
    {
      "Content-Type": contentType,
      "Content-Length": String(payload.length),
      "Cache-Control": "no-store",
    },
    payload,
  );
}

async function sendFilePath(res, targetPath, contentType) {
  const payload = await fs.readFile(targetPath);
  sendBuffer(res, payload, contentType);
}

function isWithin(parent, candidate) {
  return candidate === parent || candidate.startsWith(`${parent}${path.sep}`);
}

async function resolveBundleDir(mediaPath) {
  const candidate = path.resolve(MEDIA_ROOT, mediaPath);
  const mediaRoot = path.resolve(MEDIA_ROOT);
  if (!isWithin(mediaRoot, candidate)) {
    throw new Error(`Invalid media path: ${mediaPath}`);
  }
  const stats = await fs.stat(candidate);
  if (!stats.isDirectory()) {
    throw new Error(`Expected directory: ${candidate}`);
  }
  return candidate;
}

function itemMatchesQuery(item, query) {
  if (!query) {
    return true;
  }
  const haystack = [
    item.title || "",
    item.alt || "",
    item.caption || "",
    item.description || "",
    (item.tags || []).join(", "),
    (item.galleries || []).join(", "),
    item.original_filename || "",
    item.old_url || "",
    item.media_path || "",
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query.toLowerCase());
}

async function collectItems({ query = "", missingAlt = false, duplicatesOnly = false } = {}) {
  const items = await listMediaItems(MEDIA_ROOT);
  const duplicateGroups = buildDuplicateGroups(items);
  const duplicateSizes = new Map();
  for (const mediaPaths of Object.values(duplicateGroups)) {
    for (const mediaPath of mediaPaths) {
      duplicateSizes.set(mediaPath, mediaPaths.length);
    }
  }

  const enriched = [];
  for (const item of items) {
    const nextItem = {
      ...item,
      duplicate_group_size: duplicateSizes.get(String(item.media_path)) ?? 1,
      thumb_url: gallery_thumbnailPathForItem(item),
      image_url: gallery_mediaFilePathForItem(item),
    };
    if (missingAlt && !nextItem.missing_alt) {
      continue;
    }
    if (duplicatesOnly && nextItem.duplicate_group_size < 2) {
      continue;
    }
    if (!itemMatchesQuery(nextItem, query)) {
      continue;
    }
    enriched.push(nextItem);
  }

  enriched.sort((left, right) => {
    const leftKey = `${left.date || ""}\u0000${left.title || ""}`;
    const rightKey = `${right.date || ""}\u0000${right.title || ""}`;
    return rightKey.localeCompare(leftKey);
  });
  return enriched;
}

function gallery_thumbnailPathForItem(item) {
  return galleryThumbnailPath(path.join(MEDIA_ROOT, String(item.media_path)));
}

function gallery_mediaFilePathForItem(item) {
  return galleryMediaFilePath(path.join(MEDIA_ROOT, String(item.media_path)));
}

function placeholderThumbnailBytes(originalPath, size, kind) {
  const label = kind === "video" ? "VIDEO" : "DATEI";
  const extension = path.extname(originalPath).replace(/^\./, "").toUpperCase() || kind.toUpperCase();
  const svg = `
<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 400 300">
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
        font-size="28" font-weight="700" fill="#1e281d">${label}</text>
  <text x="200" y="236" text-anchor="middle" font-family="IBM Plex Sans, Segoe UI, sans-serif"
        font-size="18" fill="#566553">${extension}</text>
</svg>
`.trim();
  return { payload: Buffer.from(svg, "utf8"), contentType: "image/svg+xml" };
}

async function thumbnailBytes(bundleDir, size) {
  const originalPath = await originalFileForBundle(bundleDir);
  if (!originalPath) {
    throw new Error(`Missing original file in ${bundleDir}`);
  }

  const originalDetails = await inspectMediaFile(originalPath);
  const originalMime = originalDetails.mime_type || "application/octet-stream";
  const mediaKind = mediaKindForMime(originalMime);
  if (mediaKind !== "image") {
    return placeholderThumbnailBytes(originalPath, size, mediaKind);
  }

  const stats = await fs.stat(originalPath);
  const cacheKey = `${originalPath}:${size}:${stats.mtimeMs}`;
  const cached = THUMBNAIL_CACHE.get(cacheKey);
  if (cached) {
    return cached;
  }

  try {
    const payload = await sharp(originalPath)
      .rotate()
      .resize({ width: size, height: size, fit: "inside", withoutEnlargement: true })
      .png()
      .toBuffer();
    const result = { payload, contentType: "image/png" };
    THUMBNAIL_CACHE.set(cacheKey, result);
    return result;
  } catch {
    const payload = await fs.readFile(originalPath);
    const result = { payload, contentType: originalMime };
    THUMBNAIL_CACHE.set(cacheKey, result);
    return result;
  }
}

async function readRequestBody(req) {
  const chunks = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return Buffer.concat(chunks);
}

async function parseJsonRequest(req) {
  const body = await readRequestBody(req);
  return JSON.parse(body.toString("utf8"));
}

async function parseUploadedFiles(req) {
  const contentType = req.headers["content-type"] || "";
  const body = await readRequestBody(req);
  const request = new Request("http://127.0.0.1/upload", {
    method: "POST",
    headers: { "content-type": contentType },
    body,
  });
  const formData = await request.formData();
  return formData.getAll("files").filter((value) => value && typeof value.name === "string");
}

async function serveStatic(res, name, contentType) {
  const payload = await fs.readFile(path.join(UI_ROOT, name));
  send(
    res,
    200,
    {
      "Content-Type": contentType,
      "Content-Length": String(payload.length),
    },
    payload,
  );
}

async function handleListImages(reqUrl, res) {
  const items = await collectItems({
    query: reqUrl.searchParams.get("q") || "",
    missingAlt: reqUrl.searchParams.get("missing_alt") === "1",
    duplicatesOnly: reqUrl.searchParams.get("duplicates") === "1",
  });
  sendJson(res, { items });
}

async function handleThumbnail(reqUrl, res) {
  const mediaPath = decodeURIComponent(reqUrl.pathname.slice("/thumb/".length));
  const size = Number.parseInt(reqUrl.searchParams.get("size") || "320", 10) || 320;
  const bundleDir = await resolveBundleDir(mediaPath);
  const { payload, contentType } = await thumbnailBytes(bundleDir, size);
  sendBuffer(res, payload, contentType);
}

async function handleMediaFile(reqUrl, res) {
  const mediaPath = decodeURIComponent(
    reqUrl.pathname.startsWith("/media-file/")
      ? reqUrl.pathname.slice("/media-file/".length)
      : reqUrl.pathname.slice("/original/".length),
  );
  const bundleDir = await resolveBundleDir(mediaPath);
  const originalPath = await originalFileForBundle(bundleDir);
  if (!originalPath) {
    sendText(res, "Not found", 404);
    return;
  }
  const details = await inspectMediaFile(originalPath);
  await sendFilePath(res, originalPath, details.mime_type || "application/octet-stream");
}

async function handleSaveItem(req, res) {
  const payload = await parseJsonRequest(req);
  const mediaPath = String(payload.path || "");
  const bundleDir = await resolveBundleDir(mediaPath);
  const current = await loadBundle(bundleDir);

  const updatedTitle =
    String(payload.title || "").trim() ||
    titleFromFilename(String(current.original_filename || current.resource_filename || "image"));
  const metadataUpdates = {
    title: updatedTitle,
    alt: String(payload.alt || ""),
    description: String(payload.description || ""),
    tags: normalizeCommaList(payload.tags),
  };

  await saveBundleMetadata(bundleDir, metadataUpdates);
  const item = await loadBundle(bundleDir);
  const duplicates = buildDuplicateGroups(await collectItems());
  item.duplicate_group_size =
    Object.values(duplicates).find((paths) => paths.includes(item.media_path))?.length ?? 1;
  sendJson(res, { item });
}

async function handleDeleteItem(req, res) {
  const payload = await parseJsonRequest(req);
  const mediaPath = String(payload.path || "");
  const bundleDir = await resolveBundleDir(mediaPath);
  await deleteBundle(bundleDir);
  sendJson(res, { deleted: mediaPath });
}

async function handleUpload(req, res) {
  const files = await parseUploadedFiles(req);
  const created = [];
  for (const file of files) {
    const sourceBuffer = Buffer.from(await file.arrayBuffer());
    const bundleDir = await createBundleFromMedia({
      sourceBuffer,
      sourceFilename: file.name,
      title: null,
      dateValue: null,
    });
    created.push(relativeMediaPath(bundleDir));
  }
  sendJson(res, { created }, 201);
}

function sendNotFound(res) {
  sendText(res, STATUS_CODES[404] || "Not Found", 404);
}

async function handleRequest(req, res) {
  const reqUrl = new URL(req.url || "/", "http://127.0.0.1");

  if (req.method === "GET" && reqUrl.pathname === "/") {
    await serveStatic(res, "index.html", "text/html; charset=utf-8");
    return;
  }
  if (req.method === "GET" && reqUrl.pathname === "/styles.css") {
    await serveStatic(res, "styles.css", "text/css; charset=utf-8");
    return;
  }
  if (req.method === "GET" && reqUrl.pathname === "/app.js") {
    await serveStatic(res, "app.js", "application/javascript; charset=utf-8");
    return;
  }
  if (req.method === "GET" && reqUrl.pathname === "/api/images") {
    await handleListImages(reqUrl, res);
    return;
  }
  if (req.method === "GET" && reqUrl.pathname.startsWith("/thumb/")) {
    await handleThumbnail(reqUrl, res);
    return;
  }
  if (req.method === "GET" && (reqUrl.pathname.startsWith("/media-file/") || reqUrl.pathname.startsWith("/original/"))) {
    await handleMediaFile(reqUrl, res);
    return;
  }
  if (req.method === "POST" && reqUrl.pathname === "/api/item/save") {
    await handleSaveItem(req, res);
    return;
  }
  if (req.method === "POST" && reqUrl.pathname === "/api/item/delete") {
    await handleDeleteItem(req, res);
    return;
  }
  if (req.method === "POST" && reqUrl.pathname === "/api/upload") {
    await handleUpload(req, res);
    return;
  }

  sendNotFound(res);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.summary) {
    const items = await listMediaItems(MEDIA_ROOT);
    const duplicates = buildDuplicateGroups(items);
    console.log(
      JSON.stringify(
        {
          items: items.length,
          duplicate_groups: Object.keys(duplicates).length,
          media_root: path.relative(ROOT, MEDIA_ROOT).split(path.sep).join(path.posix.sep),
        },
        null,
        2,
      ),
    );
    return;
  }

  const server = createServer((req, res) => {
    handleRequest(req, res).catch((error) => {
      const message = error instanceof Error ? error.message : String(error);
      sendText(res, message, 400);
    });
  });

  server.listen(args.port, args.host, () => {
    console.log(`Media editor listening on http://${args.host}:${args.port}`);
  });

  process.on("SIGINT", () => {
    server.close(() => process.exit(0));
  });
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error));
  process.exit(1);
});
