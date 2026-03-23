#!/usr/bin/env python3

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import yaml
from bs4 import BeautifulSoup, NavigableString, Tag


ARCHIVE_URL = "https://www.fsv-unterjesingen.de/startseite/der-verein/aktuelles/"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "content" / "blog"
MANIFEST_PATH = ROOT / "artifacts" / "legacy-blog-manifest.json"
USER_AGENT = "Mozilla/5.0 (compatible; legacy-blog-migrator/1.0)"
TIMEOUT_SECONDS = 30
BLOCK_LEVEL_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "details",
    "dl",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "iframe",
    "img",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "ul",
    "video",
}


@dataclass(frozen=True)
class ArchivePost:
    title: str
    url: str
    published_at: str
    archive_page: str
    preview_image_url: str


class BundleImages:
    def __init__(self, bundle_dir: Path) -> None:
        self.bundle_dir = bundle_dir
        self._by_source_url: dict[str, str] = {}

    def register(self, source_url: str) -> str:
        if source_url in self._by_source_url:
            return self._by_source_url[source_url]

        parsed = urlparse(source_url)
        suffix = Path(parsed.path).suffix.lower() or ".bin"
        local_name = f"image-{len(self._by_source_url) + 1:02d}{suffix}"
        self._by_source_url[source_url] = local_name
        return local_name

    def items(self) -> Iterable[tuple[str, str]]:
        return self._by_source_url.items()

    def is_local_name(self, value: str) -> bool:
        return value in self._by_source_url.values()

    def is_empty(self) -> bool:
        return not self._by_source_url


def fetch_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read()


def fetch_html(url: str) -> BeautifulSoup:
    return BeautifulSoup(fetch_bytes(url), "html.parser")


def crawl_archive(start_url: str) -> list[ArchivePost]:
    seen_archive_pages: set[str] = set()
    seen_posts: set[str] = set()
    posts: list[ArchivePost] = []
    next_url = start_url

    while next_url and next_url not in seen_archive_pages:
        seen_archive_pages.add(next_url)
        soup = fetch_html(next_url)

        for article in soup.select("article"):
            title_link = article.select_one("h1 a, h2 a, h3 a")
            published = article.select_one("[itemprop='datePublished']")
            if not title_link or not published:
                continue

            post_url = title_link.get("href", "").strip()
            if not post_url or post_url in seen_posts:
                continue

            seen_posts.add(post_url)
            posts.append(
                ArchivePost(
                    title=title_link.get_text("", strip=True),
                    url=post_url,
                    published_at=published.get("datetime", "").strip(),
                    archive_page=next_url,
                    preview_image_url=extract_archive_preview_image_url(article),
                )
            )

        next_link = soup.select_one("a.inactive.next_page")
        next_url = next_link.get("href", "").strip() if next_link else ""

    return posts


def get_slug(post_url: str) -> str:
    parts = [part for part in urlparse(post_url).path.split("/") if part]
    if not parts:
        raise ValueError(f"Could not derive slug from {post_url}")
    return parts[-1]


def extract_best_image_source(img: Tag) -> str:
    for attr in ("data-srcset", "srcset"):
        srcset = img.get(attr, "").strip()
        if not srcset:
            continue

        best_url = ""
        best_width = -1
        for entry in srcset.split(","):
            parts = entry.strip().split()
            if not parts:
                continue
            candidate_url = parts[0]
            candidate_width = -1
            if len(parts) > 1 and parts[1].endswith("w") and parts[1][:-1].isdigit():
                candidate_width = int(parts[1][:-1])
            if candidate_width > best_width:
                best_url = candidate_url
                best_width = candidate_width
        if best_url:
            return best_url

    for attr in ("data-src", "data-lazy-src", "src"):
        source = img.get(attr, "").strip()
        if source:
            return source

    return ""


def extract_archive_preview_image_url(article: Tag) -> str:
    preview = article.select_one(".blog-meta img, a.small-preview img")
    if not preview:
        return ""
    return extract_best_image_source(preview)


def extract_title(soup: BeautifulSoup, post_root: Tag, archive_title: str) -> str:
    for selector in ("meta[property='og:title']", "meta[name='twitter:title']"):
        meta = soup.select_one(selector)
        if meta:
            text = meta.get("content", "").strip()
            if text:
                return text

    if soup.title:
        text = soup.title.get_text("", strip=True)
        if text:
            return re.sub(r"\s+–\s+FSV Unterjesingen e\.V\.$", "", text).strip()

    heading = post_root.select_one(".av-special-heading-tag, h1.entry-title, h2.entry-title, h3.entry-title")
    if heading:
        text = heading.get_text("", strip=True)
        if text:
            return text

    return archive_title


def copy_children_into_paragraph(source: Tag, paragraph: Tag) -> None:
    for child in list(source.contents):
        paragraph.append(child.extract())


def make_local_img_tag(fragment: BeautifulSoup, source_url: str, original_img: Tag | None, images: BundleImages) -> Tag:
    local_name = images.register(source_url)
    new_img = fragment.new_tag("img", src=local_name)

    if original_img:
        alt_text = original_img.get("alt", "")
        if alt_text:
            new_img["alt"] = alt_text

    return new_img


def find_best_image_url(tag: Tag) -> str | None:
    anchor = tag.find("a", href=True)
    if anchor and is_image_url(anchor["href"]):
        return anchor["href"].strip()

    image = tag.find("img")
    if image:
        for attr in ("data-src", "data-lazy-src", "src"):
            value = image.get(attr, "").strip()
            if value:
                return value

    return None


def is_image_url(url: str) -> bool:
    return bool(re.search(r"\.(?:avif|gif|jpe?g|png|svg|webp)(?:$|\?)", url, re.IGNORECASE))


def unwrap_anchor_images(fragment: BeautifulSoup, images: BundleImages) -> None:
    for anchor in list(fragment.find_all("a", href=True)):
        significant_children = [child for child in anchor.children if not isinstance(child, NavigableString) or child.strip()]
        if len(significant_children) != 1:
            continue

        child = significant_children[0]
        if not isinstance(child, Tag) or child.name != "img":
            continue

        source_url = anchor["href"].strip() if is_image_url(anchor["href"]) else child.get("src", "").strip()
        if not source_url:
            continue

        child["src"] = images.register(source_url)
        for attr in ("class", "width", "height", "loading", "decoding", "aria-describedby", "srcset", "sizes"):
            child.attrs.pop(attr, None)
        anchor.replace_with(child.extract())


def replace_wp_captions(fragment: BeautifulSoup, images: BundleImages) -> None:
    for caption in list(fragment.select("div.wp-caption")):
        source_url = find_best_image_url(caption)
        original_img = caption.find("img")
        if not source_url:
            caption.unwrap()
            continue

        image_paragraph = fragment.new_tag("p")
        image_paragraph.append(make_local_img_tag(fragment, source_url, original_img, images))
        caption.insert_before(image_paragraph)

        caption_text = caption.select_one(".wp-caption-text")
        if caption_text and caption_text.get_text("", strip=True):
            caption_paragraph = fragment.new_tag("p")
            copy_children_into_paragraph(caption_text, caption_paragraph)
            image_paragraph.insert_after(caption_paragraph)

        caption.decompose()


def replace_figures(fragment: BeautifulSoup, images: BundleImages) -> None:
    for figure in list(fragment.find_all("figure")):
        source_url = find_best_image_url(figure)
        original_img = figure.find("img")

        if not source_url or not original_img:
            figure.unwrap()
            continue

        image_paragraph = fragment.new_tag("p")
        image_paragraph.append(make_local_img_tag(fragment, source_url, original_img, images))
        figure.insert_before(image_paragraph)

        caption = figure.find("figcaption")
        if caption and caption.get_text("", strip=True):
            caption_paragraph = fragment.new_tag("p")
            copy_children_into_paragraph(caption, caption_paragraph)
            image_paragraph.insert_after(caption_paragraph)

        figure.decompose()


def simplify_fragment(fragment: BeautifulSoup, images: BundleImages) -> None:
    replace_wp_captions(fragment, images)
    replace_figures(fragment, images)
    unwrap_anchor_images(fragment, images)

    for anchor in fragment.find_all("a", href=True):
        anchor["href"] = anchor.get("href", "").strip()
        for attr in list(anchor.attrs):
            if attr not in {"href", "title"}:
                anchor.attrs.pop(attr, None)

    for image in fragment.find_all("img"):
        source_url = ""
        for attr in ("data-src", "data-lazy-src", "src"):
            value = image.get(attr, "").strip()
            if value:
                source_url = value
                break
        if not source_url:
            continue
        if images.is_local_name(source_url):
            continue

        image["src"] = images.register(source_url)
        for attr in ("class", "width", "height", "loading", "decoding", "aria-describedby", "srcset", "sizes", "style", "id"):
            image.attrs.pop(attr, None)

    for container in list(fragment.find_all(["div", "section"])):
        child_tags = [child for child in container.children if isinstance(child, Tag)]
        has_non_whitespace_text = any(
            isinstance(child, NavigableString) and child.strip()
            for child in container.children
        )

        if not child_tags and not has_non_whitespace_text:
            container.decompose()
            continue

        if not child_tags:
            paragraph = fragment.new_tag("p")
            copy_children_into_paragraph(container, paragraph)
            container.replace_with(paragraph)
            continue

        if has_non_whitespace_text and any(child.name in BLOCK_LEVEL_TAGS for child in child_tags):
            paragraph = fragment.new_tag("p")
            for child in list(container.contents):
                if isinstance(child, NavigableString):
                    if child.strip():
                        paragraph.append(child.extract())
                else:
                    container.insert_before(child.extract())
            if paragraph.get_text("", strip=True):
                container.insert_before(paragraph)
            container.decompose()
            continue

        container.unwrap()

    for span in list(fragment.find_all("span")):
        if span.attrs:
            span.unwrap()

    for text_node in fragment.find_all(string=True):
        if "\xa0" in text_node:
            text_node.replace_with(text_node.replace("\xa0", " "))


def convert_html_to_markdown(html_fragment: str) -> str:
    result = subprocess.run(
        ["pandoc", "--from=html", "--to=gfm", "--wrap=none"],
        input=html_fragment.encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    markdown = result.stdout.decode("utf-8")
    return markdown.strip()


def heading_markdown(heading: Tag) -> str:
    level = heading.name.lower()
    depth = int(level[1]) if re.fullmatch(r"h[1-6]", level) else 2
    return f"{'#' * depth} {heading.get_text('', strip=True)}"


def render_body(post_url: str, archive_title: str) -> tuple[str, BundleImages, list[str], str]:
    soup = fetch_html(post_url)
    post_root = soup.select_one("main .post-entry")
    if not post_root:
        raise RuntimeError(f"Could not find post root for {post_url}")

    title = extract_title(soup, post_root, archive_title)
    slug = get_slug(post_url)
    bundle_dir = OUTPUT_DIR / slug
    images = BundleImages(bundle_dir)
    blocks: list[str] = []
    warnings: list[str] = []
    skipped_title_heading = False

    def render_html_block(html: str) -> None:
        fragment = BeautifulSoup(html, "html.parser")
        simplify_fragment(fragment, images)
        markdown = convert_html_to_markdown(str(fragment))
        if markdown:
            blocks.append(markdown)

    def render_featured_image(node: Tag) -> None:
        image = node.select_one("a[href] img, img")
        if not image:
            return

        link = node.select_one("a[href]")
        source_url = ""
        if link and is_image_url(link.get("href", "")):
            source_url = link["href"].strip()
        else:
            source_url = image.get("src", "").strip()
        if not source_url:
            return

        fragment = BeautifulSoup("", "html.parser")
        paragraph = fragment.new_tag("p")
        paragraph.append(make_local_img_tag(fragment, source_url, image, images))
        fragment.append(paragraph)
        render_html_block(str(fragment))

    def render_masonry_gallery(node: Tag) -> None:
        fragment = BeautifulSoup("", "html.parser")
        for anchor in node.select("a[href]"):
            image = anchor.find("img")
            source_url = ""
            if is_image_url(anchor.get("href", "")):
                source_url = anchor["href"].strip()
            elif image:
                source_url = image.get("src", "").strip()
            if not source_url:
                continue

            paragraph = fragment.new_tag("p")
            paragraph.append(make_local_img_tag(fragment, source_url, image, images))
            fragment.append(paragraph)

        if fragment.contents:
            render_html_block(str(fragment))

    def render_slideshow(node: Tag) -> None:
        fragment = BeautifulSoup("", "html.parser")
        for image in node.select("li.avia-slideshow-slide img, .avia-slideshow-inner img"):
            source_url = image.get("src", "").strip()
            if not source_url:
                continue

            paragraph = fragment.new_tag("p")
            paragraph.append(make_local_img_tag(fragment, source_url, image, images))
            fragment.append(paragraph)

        if fragment.contents:
            render_html_block(str(fragment))

    def process_node(node: Tag) -> None:
        nonlocal skipped_title_heading

        classes = set(node.get("class", []))

        if node.name in {"style", "script"}:
            return

        if node.name in {"header", "footer"} or classes & {
            "entry-footer",
            "post-meta-infos",
            "post_delimiter",
            "av-vertical-delimiter",
            "av-social-sharing-box",
            "special-heading-border",
            "av-share-box",
            "av-share-box-list",
            "iconfont",
        }:
            return

        if "blog-meta" in classes:
            render_featured_image(node)
            return

        if "avia-image-container" in classes:
            render_featured_image(node)
            return

        if "av-masonry" in classes:
            render_masonry_gallery(node)
            return

        if "avia-slideshow" in classes:
            render_slideshow(node)
            return

        if "av-special-heading" in classes:
            heading = node.find(re.compile(r"^h[1-6]$"))
            if not heading:
                return

            heading_text = heading.get_text("", strip=True)
            if not skipped_title_heading and (heading_text == title or title.startswith(heading_text)):
                skipped_title_heading = True
                return

            blocks.append(heading_markdown(heading))
            return

        if "av_textblock_section" in classes:
            text_block = node.select_one(".avia_textblock")
            if text_block:
                render_html_block(text_block.decode_contents())
            return

        if "entry-content" in classes:
            render_html_block(node.decode_contents())
            return

        if "hr" in classes or node.name == "span":
            return

        child_tags = [child for child in node.children if isinstance(child, Tag)]
        if child_tags:
            for child in child_tags:
                process_node(child)
            return

        if node.get_text("", strip=True):
            render_html_block(str(node))

    for child in [child for child in post_root.children if isinstance(child, Tag)]:
        process_node(child)

    body = "\n\n".join(block.strip() for block in blocks if block.strip()).strip() + "\n"

    raw_html_markers = []
    for marker in ("<div", "<span", "<section", "<iframe", "<video", "<table", "<figure"):
        if marker in body:
            raw_html_markers.append(marker)
    if raw_html_markers:
        warnings.append(f"Raw HTML remained in {post_url}: {', '.join(raw_html_markers)}")

    return body, images, warnings, title


def write_frontmatter_markdown(bundle_dir: Path, title: str, published_at: str, alias: str, body: str) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    frontmatter = {
        "title": title,
        "date": published_at,
        "aliases": [alias],
    }
    frontmatter_yaml = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).strip()
    content = f"---\n{frontmatter_yaml}\n---\n\n{body}"
    (bundle_dir / "index.md").write_text(content, encoding="utf-8")


def download_images(bundle_dir: Path, images: BundleImages) -> list[str]:
    written = []
    for source_url, local_name in images.items():
        target = bundle_dir / local_name
        candidate_urls = [urljoin(ARCHIVE_URL, source_url)]

        primary_url = candidate_urls[0]
        parsed = urlparse(primary_url)
        if parsed.netloc.endswith("daec.de") and "/fileadmin/user_upload/files/" in parsed.path:
            candidate_urls.append(primary_url.replace("/fileadmin/user_upload/files/", "/media/files/"))

        last_error: Exception | None = None
        for resolved_url in candidate_urls:
            try:
                target.write_bytes(fetch_bytes(resolved_url))
                break
            except Exception as exc:
                last_error = exc
        else:
            raise RuntimeError(f"Failed to download {' or '.join(candidate_urls)} -> {target}") from last_error
        written.append(local_name)
    return written


def migrate() -> int:
    posts = crawl_archive(ARCHIVE_URL)
    manifest = []
    warnings: list[str] = []

    for index, post in enumerate(posts, start=1):
        slug = get_slug(post.url)
        bundle_dir = OUTPUT_DIR / slug
        body, images, post_warnings, resolved_title = render_body(post.url, post.title)
        if images.is_empty() and post.preview_image_url:
            local_name = images.register(post.preview_image_url)
            body = body.rstrip() + f"\n\n![]({local_name})\n"
        write_frontmatter_markdown(bundle_dir, resolved_title, post.published_at, urlparse(post.url).path, body)
        written_images = download_images(bundle_dir, images)
        warnings.extend(post_warnings)

        manifest.append(
            {
                "index": index,
                "title": resolved_title,
                "url": post.url,
                "alias": urlparse(post.url).path,
                "date": post.published_at,
                "bundle": str(bundle_dir.relative_to(ROOT)),
                "images": written_images,
            }
        )
        print(f"[{index:02d}/{len(posts):02d}] migrated {post.url} -> {bundle_dir.relative_to(ROOT)}")

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if warnings:
        warning_text = "\n".join(sorted(set(warnings)))
        (ROOT / "artifacts" / "legacy-blog-warnings.txt").write_text(warning_text + "\n", encoding="utf-8")
        print("\nWarnings were written to artifacts/legacy-blog-warnings.txt", file=sys.stderr)
        return 1

    warning_path = ROOT / "artifacts" / "legacy-blog-warnings.txt"
    if warning_path.exists():
        warning_path.unlink()

    print(f"\nMigrated {len(posts)} posts. Manifest written to {MANIFEST_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(migrate())
