import { createHash } from "node:crypto";
import { createReadStream } from "node:fs";
import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";
import { parse as parseYaml, stringify as stringifyYaml } from "yaml";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(SCRIPT_DIR, "..");
export const MEDIA_ROOT = path.join(ROOT, "content", "media");
export const MEDIA_RESOURCE_BASENAME = "media";

const FRONT_MATTER_ORDER = [
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
];

const SECTION_FRONT_MATTER = {
  title: "Media",
  build: { render: "never", list: "always", publishResources: true },
};

const WORDPRESS_IMAGE_META_TEXT_KEYS = new Set(["credit", "camera", "caption", "copyright", "title"]);
const WORDPRESS_IMAGE_META_NUMERIC_KEYS = new Set([
  "aperture",
  "created_timestamp",
  "focal_length",
  "iso",
  "shutter_speed",
  "orientation",
]);
const WORDPRESS_IMAGE_META_KEYS = new Set([
  ...WORDPRESS_IMAGE_META_TEXT_KEYS,
  ...WORDPRESS_IMAGE_META_NUMERIC_KEYS,
  "keywords",
]);

const MIME_TYPES = new Map([
  [".avif", "image/avif"],
  [".gif", "image/gif"],
  [".heic", "image/heic"],
  [".heif", "image/heif"],
  [".jpeg", "image/jpeg"],
  [".jpg", "image/jpeg"],
  [".jxl", "image/jxl"],
  [".mov", "video/quicktime"],
  [".mp4", "video/mp4"],
  [".m4v", "video/x-m4v"],
  [".pdf", "application/pdf"],
  [".png", "image/png"],
  [".svg", "image/svg+xml"],
  [".webm", "video/webm"],
  [".webp", "image/webp"],
]);

function posixRelative(from, to) {
  return path.relative(from, to).split(path.sep).join(path.posix.sep);
}

function pad(number) {
  return String(number).padStart(2, "0");
}

function formatDateOnly(value) {
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
}

export function formatTimestamp(value) {
  if (!(value instanceof Date) || Number.isNaN(value.getTime())) {
    return null;
  }
  return (
    `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}` +
    `T${pad(value.getHours())}:${pad(value.getMinutes())}:${pad(value.getSeconds())}`
  );
}

export function parseTimestamp(value) {
  const text = String(value ?? "").trim();
  if (!text) {
    return null;
  }

  let match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (match) {
    return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 0, 0, 0);
  }

  match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$/.exec(text);
  if (match) {
    return new Date(
      Number(match[1]),
      Number(match[2]) - 1,
      Number(match[3]),
      Number(match[4]),
      Number(match[5]),
      Number(match[6] ?? "0"),
    );
  }

  return null;
}

export function toDatetimeLocalValue(value) {
  const timestamp = parseTimestamp(value);
  return timestamp ? formatTimestamp(timestamp) ?? "" : "";
}

function cleanImageMetaText(value) {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value).replaceAll("\0", "").trim();
}

function isDefaultImageMetaValue(key, value) {
  if (value === null || value === undefined || value === false) {
    return true;
  }

  if (key === "keywords") {
    if (Array.isArray(value)) {
      return !value.some((item) => cleanImageMetaText(item));
    }
    return !cleanImageMetaText(value);
  }

  if (WORDPRESS_IMAGE_META_TEXT_KEYS.has(key)) {
    return !cleanImageMetaText(value);
  }

  const numeric = Number.parseFloat(cleanImageMetaText(value) || "0");
  if (Number.isNaN(numeric)) {
    return !cleanImageMetaText(value);
  }
  return numeric === 0;
}

export function meaningfulImageMetaEntries(imageMeta) {
  const payload = imageMeta && typeof imageMeta === "object" ? { ...imageMeta } : {};
  return Object.fromEntries(
    Object.entries(payload).filter(
      ([key, value]) => WORDPRESS_IMAGE_META_KEYS.has(key) && !isDefaultImageMetaValue(key, value),
    ),
  );
}

export function mediaKindForMime(mimeType) {
  const clean = String(mimeType ?? "").trim().toLowerCase();
  if (clean.startsWith("image/")) {
    return "image";
  }
  if (clean.startsWith("video/")) {
    return "video";
  }
  return "file";
}

function mimeTypeForPath(filePath) {
  return MIME_TYPES.get(path.extname(filePath).toLowerCase()) ?? "application/octet-stream";
}

export function titleFromFilename(filename) {
  const stem = path.parse(filename).name;
  const cleaned = stem.replace(/[_-]+/g, " ").trim();
  if (!cleaned) {
    return "Untitled media";
  }
  return cleaned[0].toUpperCase() + cleaned.slice(1);
}

export function normalizeCommaList(value) {
  if (value === null || value === undefined) {
    return [];
  }
  const items = Array.isArray(value) ? value : String(value).split(",").map((part) => part.trim());
  return items.map((item) => String(item).trim()).filter(Boolean);
}

export function parseFrontMatter(text) {
  if (!text.startsWith("---\n")) {
    return [{}, text];
  }

  const endIndex = text.indexOf("\n---\n", 4);
  if (endIndex < 0) {
    return [{}, text];
  }

  const frontMatter = text.slice(4, endIndex);
  const body = text.slice(endIndex + 5);
  const data = parseYaml(frontMatter) ?? {};
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("Expected YAML front matter to deserialize to a mapping");
  }
  return [data, body];
}

export function dumpFrontMatter(data, body = "") {
  const ordered = {};
  for (const key of FRONT_MATTER_ORDER) {
    if (Object.hasOwn(data, key)) {
      ordered[key] = data[key];
    }
  }
  for (const [key, value] of Object.entries(data)) {
    if (!Object.hasOwn(ordered, key)) {
      ordered[key] = value;
    }
  }

  const payload = stringifyYaml(ordered, {
    lineWidth: 100,
    simpleKeys: true,
    singleQuote: true,
  }).trimEnd();
  const cleanBody = body.replace(/^\n+/, "");
  if (cleanBody) {
    return `---\n${payload}\n---\n\n${cleanBody}`;
  }
  return `---\n${payload}\n---\n`;
}

export async function writeMarkdownFile(targetPath, frontMatter, body = "") {
  await fs.mkdir(path.dirname(targetPath), { recursive: true });
  await fs.writeFile(targetPath, dumpFrontMatter(frontMatter, body), "utf8");
}

export async function readMarkdownFile(targetPath) {
  const text = await fs.readFile(targetPath, "utf8");
  return parseFrontMatter(text);
}

export async function sha256File(targetPath) {
  const digest = createHash("sha256");
  await new Promise((resolve, reject) => {
    const stream = createReadStream(targetPath);
    stream.on("data", (chunk) => digest.update(chunk));
    stream.on("error", reject);
    stream.on("end", resolve);
  });
  return digest.digest("hex");
}

async function inspectImage(targetPath) {
  try {
    const metadata = await sharp(targetPath).metadata();
    const format = String(metadata.format ?? "").trim().toLowerCase();
    const mimeByFormat = MIME_TYPES.get(`.${format === "jpeg" ? "jpg" : format}`) ?? null;
    return {
      width: Number.isFinite(metadata.width) ? metadata.width : null,
      height: Number.isFinite(metadata.height) ? metadata.height : null,
      mimeType: mimeByFormat,
    };
  } catch {
    return null;
  }
}

export async function inspectMediaFile(targetPath) {
  const stat = await fs.stat(targetPath);
  const details = {
    filesize: stat.size,
    width: null,
    height: null,
    mime_type: mimeTypeForPath(targetPath),
  };

  if (mediaKindForMime(details.mime_type) !== "image") {
    return details;
  }

  const inspected = await inspectImage(targetPath);
  if (inspected) {
    details.width = inspected.width;
    details.height = inspected.height;
    details.mime_type = inspected.mimeType ?? details.mime_type;
  }
  return details;
}

export function canonicalizeBundleFrontMatter(frontMatter) {
  const updated = { ...frontMatter };
  const legacyBuild = updated._build;
  delete updated._build;
  if (legacyBuild !== undefined && !Object.hasOwn(updated, "build")) {
    updated.build = legacyBuild;
  }

  const legacyWordpress = updated.wordpress && typeof updated.wordpress === "object" ? { ...updated.wordpress } : {};
  const legacyGuid = cleanImageMetaText(legacyWordpress.guid);
  const currentOldUrl = cleanImageMetaText(updated.old_url);
  if (legacyGuid && !currentOldUrl) {
    updated.old_url = legacyGuid;
  } else if (currentOldUrl) {
    updated.old_url = currentOldUrl;
  }

  const sourceFilename = cleanImageMetaText(updated.source_filename);
  delete updated.source_filename;
  const currentOriginalFilename = cleanImageMetaText(updated.original_filename);
  if (sourceFilename) {
    updated.original_filename = sourceFilename;
  } else if (!currentOriginalFilename) {
    delete updated.original_filename;
  }

  for (const redundantKey of ["source_relative_path", "mime_type", "width", "height", "filesize", "sha256"]) {
    delete updated[redundantKey];
  }

  const dateValue = cleanImageMetaText(updated.date);
  const lastmodValue = cleanImageMetaText(updated.lastmod);
  if (dateValue && dateValue === cleanImageMetaText(updated.uploaded_at_local)) {
    delete updated.uploaded_at_local;
    delete updated.uploaded_at_gmt;
  }
  if (lastmodValue && lastmodValue === cleanImageMetaText(updated.modified_at_local)) {
    delete updated.modified_at_local;
    delete updated.modified_at_gmt;
  }

  if (Object.hasOwn(updated, "image_meta")) {
    const meaningful = meaningfulImageMetaEntries(updated.image_meta);
    if (Object.keys(meaningful).length > 0) {
      updated.image_meta = meaningful;
    } else {
      delete updated.image_meta;
    }
  }

  for (const key of Object.keys(updated)) {
    if (key === "build") {
      continue;
    }

    const value = updated[key];
    if (value === null || value === undefined) {
      delete updated[key];
      continue;
    }
    if (typeof value === "string" && !value.trim()) {
      delete updated[key];
      continue;
    }
    if (Array.isArray(value) && value.length === 0) {
      delete updated[key];
      continue;
    }
    if (!Array.isArray(value) && typeof value === "object" && Object.keys(value).length === 0) {
      delete updated[key];
    }
  }

  return updated;
}

export function displayPeriodFolder(metadata) {
  const timestamp = parseTimestamp(metadata?.uploaded_at_local || metadata?.date || "");
  return formatDateOnly(timestamp ?? new Date());
}

export async function ensureUniqueBundleDir(parent, baseSlug) {
  let candidate = path.join(parent, baseSlug);
  try {
    await fs.access(candidate);
  } catch {
    return candidate;
  }

  let suffix = 2;
  for (;;) {
    candidate = path.join(parent, `${baseSlug}-${suffix}`);
    try {
      await fs.access(candidate);
      suffix += 1;
    } catch {
      return candidate;
    }
  }
}

export async function originalFileForBundle(bundleDir) {
  const entries = await fs.readdir(bundleDir, { withFileTypes: true });
  const sorted = [...entries].sort((left, right) => left.name.localeCompare(right.name));
  for (const basename of [`${MEDIA_RESOURCE_BASENAME}.`, "original."]) {
    for (const entry of sorted) {
      if (entry.isFile() && entry.name.startsWith(basename)) {
        return path.join(bundleDir, entry.name);
      }
    }
  }
  return null;
}

export function relativeBundlePath(bundleDir) {
  return posixRelative(ROOT, bundleDir);
}

export function relativeMediaPath(bundleDir) {
  return posixRelative(MEDIA_ROOT, bundleDir);
}

export function galleryThumbnailPath(bundleDir) {
  return `/thumb/${relativeMediaPath(bundleDir)}`;
}

export function galleryMediaFilePath(bundleDir) {
  return `/media-file/${relativeMediaPath(bundleDir)}`;
}

export async function loadBundle(bundleDir) {
  const indexPath = path.join(bundleDir, "index.md");
  const [metadataRaw, body] = await readMarkdownFile(indexPath);
  const originalPath = await originalFileForBundle(bundleDir);
  if (!originalPath) {
    throw new Error(`Missing original image in ${bundleDir}`);
  }

  const metadata = { ...metadataRaw };
  const fileDetails = await inspectMediaFile(originalPath);
  metadata.description = metadata.description || body.trim() || null;
  metadata.bundle_dir = relativeBundlePath(bundleDir);
  metadata.media_path = relativeMediaPath(bundleDir);
  metadata.image_url = galleryMediaFilePath(bundleDir);
  metadata.thumb_url = galleryThumbnailPath(bundleDir);
  metadata.original_path = posixRelative(ROOT, originalPath);
  metadata.resource_filename = path.basename(originalPath);
  metadata.original_filename =
    metadata.source_filename || metadata.original_filename || path.basename(originalPath);
  metadata.old_url = metadata.old_url || metadata.wordpress?.guid || "";
  metadata.width = metadata.width || fileDetails.width;
  metadata.height = metadata.height || fileDetails.height;
  metadata.mime_type = metadata.mime_type || fileDetails.mime_type;
  metadata.media_kind = mediaKindForMime(metadata.mime_type);
  metadata.filesize = metadata.filesize || fileDetails.filesize;
  metadata.sha256 = metadata.sha256 || (await sha256File(originalPath));
  metadata.tags = Array.isArray(metadata.tags) ? [...metadata.tags] : [];
  metadata.galleries = Array.isArray(metadata.galleries) ? [...metadata.galleries] : [];
  metadata.title =
    metadata.title || titleFromFilename(String(metadata.original_filename || path.basename(originalPath)));
  metadata.date = toDatetimeLocalValue(metadata.date);
  metadata.lastmod = toDatetimeLocalValue(metadata.lastmod);
  metadata.description = metadata.description || "";
  metadata.alt = metadata.alt || "";
  metadata.caption = metadata.caption || "";
  metadata.credit = metadata.credit || "";
  metadata.location = metadata.location || "";
  metadata.missing_alt = metadata.media_kind === "image" && !String(metadata.alt || "").trim();
  delete metadata.build;
  delete metadata.wordpress;
  return metadata;
}

async function collectBundleDirs(root) {
  const entries = await fs.readdir(root, { withFileTypes: true });
  const bundleDirs = [];
  for (const entry of entries) {
    const entryPath = path.join(root, entry.name);
    if (entry.isDirectory()) {
      bundleDirs.push(...(await collectBundleDirs(entryPath)));
      continue;
    }
    if (entry.isFile() && entry.name === "index.md") {
      bundleDirs.push(path.dirname(entryPath));
    }
  }
  return bundleDirs;
}

async function mapWithConcurrency(items, limit, mapper) {
  const results = new Array(items.length);
  let nextIndex = 0;

  async function worker() {
    for (;;) {
      const currentIndex = nextIndex;
      nextIndex += 1;
      if (currentIndex >= items.length) {
        return;
      }
      results[currentIndex] = await mapper(items[currentIndex], currentIndex);
    }
  }

  const workers = Array.from({ length: Math.min(limit, items.length) }, () => worker());
  await Promise.all(workers);
  return results;
}

export async function listBundles(root = MEDIA_ROOT) {
  try {
    await fs.access(root);
  } catch {
    return [];
  }
  return (await collectBundleDirs(root)).sort((left, right) => left.localeCompare(right));
}

export async function listMediaItems(root = MEDIA_ROOT) {
  const bundleDirs = await listBundles(root);
  const items = await mapWithConcurrency(bundleDirs, 12, (bundleDir) => loadBundle(bundleDir));
  items.sort((left, right) => {
    const leftKey = `${left.date || ""}\u0000${left.title || ""}\u0000${left.bundle_dir || ""}`;
    const rightKey = `${right.date || ""}\u0000${right.title || ""}\u0000${right.bundle_dir || ""}`;
    return leftKey.localeCompare(rightKey);
  });
  return items;
}

export async function prepareSectionRoot(root = MEDIA_ROOT) {
  await fs.mkdir(root, { recursive: true });
  const sectionIndex = path.join(root, "_index.md");
  try {
    await fs.access(sectionIndex);
  } catch {
    await writeMarkdownFile(sectionIndex, SECTION_FRONT_MATTER);
  }
}

function buildFrontMatter({
  title,
  date,
  lastmod,
  alt,
  caption,
  description,
  tags,
  galleries,
  credit,
  location,
  original_filename,
  wordpress_id,
  image_meta,
  old_url,
}) {
  const payload = {
    title,
    date,
    lastmod,
    alt: alt || "",
    caption: caption || "",
    description: description || "",
    tags: tags || [],
    galleries: galleries || [],
    credit: credit || "",
    location: location || "",
    original_filename: String(original_filename || "").trim() || null,
    old_url: String(old_url || "").trim() || null,
    wordpress_id,
    build: { render: "never", list: "always", publishResources: true },
  };
  if (image_meta !== undefined) {
    payload.image_meta = image_meta;
  }
  return canonicalizeBundleFrontMatter(
    Object.fromEntries(Object.entries(payload).filter(([, value]) => value !== null && value !== undefined)),
  );
}

export async function saveBundleMetadata(bundleDir, metadataUpdates, { touchLastmod = true } = {}) {
  const indexPath = path.join(bundleDir, "index.md");
  const [frontMatter, body] = await readMarkdownFile(indexPath);
  let nextBody = body;
  if (Object.hasOwn(metadataUpdates, "description")) {
    nextBody = "";
  }

  const merged = { ...frontMatter, ...metadataUpdates };
  merged.description = metadataUpdates.description ?? merged.description ?? "";
  merged.tags = normalizeCommaList(merged.tags);
  merged.galleries = normalizeCommaList(merged.galleries);

  if (touchLastmod) {
    merged.lastmod = formatTimestamp(new Date());
  }

  await writeMarkdownFile(indexPath, canonicalizeBundleFrontMatter(merged), nextBody);
}

async function pruneEmptyParents(start, stopAt) {
  let current = start;
  while (current !== stopAt) {
    try {
      const remaining = await fs.readdir(current);
      if (remaining.length > 0) {
        break;
      }
      await fs.rmdir(current);
      current = path.dirname(current);
    } catch {
      break;
    }
  }
}

export function staticPathForOldUrl(oldUrl, { staticRoot = path.join(ROOT, "static") } = {}) {
  const raw = String(oldUrl ?? "").trim();
  if (!raw) {
    throw new Error(`Missing path in old URL: ${JSON.stringify(oldUrl)}`);
  }

  let pathname = "";
  try {
    pathname = new URL(raw).pathname;
  } catch {
    pathname = raw;
  }

  const cleanPath = pathname.replace(/^\/+/, "");
  if (!cleanPath) {
    throw new Error(`Invalid old URL path: ${JSON.stringify(oldUrl)}`);
  }
  return path.join(staticRoot, cleanPath);
}

export function relativeSymlinkTarget(linkPath, targetPath) {
  return path.relative(path.dirname(linkPath), targetPath);
}

export async function deleteBundle(
  bundleDir,
  { mediaRoot = MEDIA_ROOT, staticRoot = path.join(ROOT, "static") } = {},
) {
  const resolvedBundleDir = path.resolve(bundleDir);
  const resolvedMediaRoot = path.resolve(mediaRoot);
  const resolvedStaticRoot = path.resolve(staticRoot);
  if (
    resolvedBundleDir !== resolvedMediaRoot &&
    !resolvedBundleDir.startsWith(`${resolvedMediaRoot}${path.sep}`)
  ) {
    throw new Error(`Refusing to delete bundle outside media root: ${resolvedBundleDir}`);
  }

  const stats = await fs.stat(resolvedBundleDir);
  if (!stats.isDirectory()) {
    throw new Error(`Expected directory: ${resolvedBundleDir}`);
  }

  const [frontMatter] = await readMarkdownFile(path.join(resolvedBundleDir, "index.md"));
  const originalPath = await originalFileForBundle(resolvedBundleDir);
  const oldUrl = cleanImageMetaText(frontMatter.old_url);

  if (oldUrl && originalPath) {
    try {
      const linkPath = staticPathForOldUrl(oldUrl, { staticRoot: resolvedStaticRoot });
      const linkStats = await fs.lstat(linkPath);
      if (linkStats.isSymbolicLink()) {
        const expectedTarget = relativeSymlinkTarget(linkPath, originalPath);
        const currentTarget = await fs.readlink(linkPath);
        if (currentTarget === expectedTarget) {
          await fs.unlink(linkPath);
          await pruneEmptyParents(path.dirname(linkPath), resolvedStaticRoot);
        }
      }
    } catch {
      // Ignore missing or unrelated legacy upload symlinks.
    }
  }

  await fs.rm(resolvedBundleDir, { recursive: true, force: true });
  await pruneEmptyParents(path.dirname(resolvedBundleDir), resolvedMediaRoot);
}

export async function createBundleFromMedia({
  sourceBuffer,
  sourceFilename,
  title,
  dateValue,
  mediaRoot = MEDIA_ROOT,
  tags = null,
  galleries = null,
}) {
  await prepareSectionRoot(mediaRoot);

  const timestamp = parseTimestamp(dateValue) ?? new Date();
  const periodFolder = formatDateOnly(timestamp).slice(0, 7);
  const bundleParent = path.join(mediaRoot, periodFolder);
  const baseTitle = title || titleFromFilename(sourceFilename);
  const bundleDir = await ensureUniqueBundleDir(bundleParent, path.parse(sourceFilename).name || "image");
  const suffix = path.extname(sourceFilename).toLowerCase();
  const originalName = suffix ? `${MEDIA_RESOURCE_BASENAME}${suffix}` : MEDIA_RESOURCE_BASENAME;

  await fs.mkdir(bundleDir, { recursive: true });
  await fs.writeFile(path.join(bundleDir, originalName), sourceBuffer);

  const frontMatter = buildFrontMatter({
    title: baseTitle,
    date: formatDateOnly(timestamp),
    lastmod: formatTimestamp(new Date()),
    alt: null,
    caption: null,
    description: null,
    tags: tags || [],
    galleries: galleries || [],
    credit: null,
    location: null,
    original_filename: sourceFilename,
    wordpress_id: null,
    image_meta: undefined,
    old_url: null,
  });
  await writeMarkdownFile(path.join(bundleDir, "index.md"), frontMatter);
  return bundleDir;
}

export function buildDuplicateGroups(items) {
  const groups = new Map();
  for (const item of items) {
    const sha256 = item?.sha256;
    if (!sha256) {
      continue;
    }
    const existing = groups.get(String(sha256)) ?? [];
    existing.push(String(item.media_path));
    groups.set(String(sha256), existing);
  }
  return Object.fromEntries(
    [...groups.entries()].filter(([, mediaPaths]) => mediaPaths.length > 1),
  );
}
