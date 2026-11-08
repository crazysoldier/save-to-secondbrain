"""
Content fetching for articles, websites, X/Twitter posts, and PDFs.

Extraction strategy (matching the OpenClaw second-brain-resource skill):
  1. Articles: Playwright (inner_html → markdownify) for full formatting + images
  2. X/Twitter: FxEmbed API for tweet metadata + Playwright with cookies for X Articles
  3. PDFs: Marker (full-fidelity) → fall back to pymupdf4llm
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse, unquote

import requests

import browser as _browser
import config

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
TIMEOUT = 15


@dataclass
class FetchedContent:
    url: str
    title: str
    text: str
    author: str = ""
    site_name: str = ""
    description: str = ""
    content_type: str = "article"  # "article" | "tweet" | "pdf"
    extra: Dict = field(default_factory=dict)


# ── URL helpers ───────────────────────────────────────────────────────────────

_TWITTER_RE = re.compile(
    r"https?://(?:www\.)?(twitter\.com|x\.com)/(\w+)/status/(\d+)",
    re.IGNORECASE,
)
_X_ARTICLE_RE = re.compile(
    r"https?://(?:www\.)?(?:twitter\.com|x\.com)/i/article/(\d+)", re.IGNORECASE
)

_FXEMBED_API = "https://api.fxtwitter.com/2/status/{tweet_id}"
_FXEMBED_WEB = "https://fxtwitter.com/{username}/status/{tweet_id}"


def is_tweet_url(url: str) -> bool:
    return bool(_TWITTER_RE.match(url))


def _parse_tweet_url(url: str) -> "tuple[str, str]":
    m = _TWITTER_RE.match(url)
    if not m:
        raise ValueError(f"Not a tweet URL: {url}")
    return m.group(2), m.group(3)


# ── Tweet fetching ────────────────────────────────────────────────────────────

def fetch_tweet(url: str) -> FetchedContent:
    username, tweet_id = _parse_tweet_url(url)

    api_url = _FXEMBED_API.format(tweet_id=tweet_id)
    display_url = _FXEMBED_WEB.format(username=username, tweet_id=tweet_id)

    resp = requests.get(api_url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    post = data.get("tweet") or data.get("status") or {}
    author = post.get("author", {})

    text = post.get("text", "").strip()
    author_name = author.get("name", "")
    author_handle = author.get("screen_name", username)
    created_at = post.get("created_at", "")

    media_items: List[Dict] = (post.get("media") or {}).get("all", [])
    media_urls: List[str] = []
    for item in media_items:
        if item.get("type") == "video":
            media_urls.append(item.get("thumbnail_url") or item.get("url", ""))
        else:
            media_urls.append(item.get("url", ""))

    # X Articles: text is empty; full content lives on x.com (login-gated)
    article = post.get("article") or {}
    x_article_url = ""
    browser_fetched = False

    if article:
        title = article.get("title", "") or f"@{author_handle} article"
        x_article_url = (
            f"https://x.com/i/article/{article['id']}" if article.get("id") else ""
        )

        cover = (article.get("cover_media") or {}).get("media_info", {})
        cover_url = cover.get("url", "") or cover.get("original_img_url", "")
        if cover_url and cover_url not in media_urls:
            media_urls.insert(0, cover_url)

        # Try to fetch full content via saved browser session
        if x_article_url and _browser.is_logged_in():
            try:
                full_text = _browser.fetch_x_article(x_article_url)
                if full_text and len(full_text) > len(
                    article.get("preview_text", "")
                ):
                    # Drop duplicate title line at the top of the body
                    ft_lines = full_text.split("\n")
                    if ft_lines and title:
                        clean_first = ft_lines[0].lstrip("#").strip()
                        if clean_first.lower() == title.lower():
                            full_text = "\n".join(ft_lines[1:]).lstrip("\n")
                    text = full_text
                    browser_fetched = True
                    logger.info("Browser fetch OK: %d chars", len(full_text))
                else:
                    logger.warning(
                        "Browser fetch returned too little text (%d chars), using preview",
                        len(full_text) if full_text else 0,
                    )
            except Exception as e:
                logger.warning(
                    "Browser fetch failed: %s — falling back to preview", e
                )

        if not browser_fetched:
            text = article.get("preview_text", "")
    else:
        title = f"@{author_handle}: {text[:60]}{'…' if len(text) > 60 else ''}"

    return FetchedContent(
        url=display_url,
        title=title,
        text=text,
        author=f"{author_name} (@{author_handle})",
        site_name="X / Twitter",
        content_type="tweet",
        extra={
            "original_url": url,
            "created_at": created_at,
            "media_urls": [u for u in media_urls if u],
            "x_article_url": x_article_url,
            "browser_fetched": browser_fetched,
        },
    )


# ── Article fetching (Playwright only) ────────────────────────────────────────

def fetch_article(url: str) -> FetchedContent:
    """
    Extract article content using Playwright (inner_html → markdownify).
    Preserves formatting, images, headings, links.
    """
    logger.info("Extracting with Playwright: %s", url)
    text = _browser.fetch_page(url)
    meta = _browser.fetch_page_metadata(url)

    title = ""
    author = ""
    description = ""
    site_name = ""
    if meta:
        title = meta.get("og_title") or meta.get("title", "")
        author = meta.get("author", "")
        description = meta.get("description", "")
        site_name = meta.get("site_name", "")

    if not title:
        title = _fallback_title_from_url(url)
    if not site_name:
        site_name = _site_name(url)

    return FetchedContent(
        url=url,
        title=title,
        text=text or "",
        author=author,
        site_name=site_name,
        description=description,
        content_type="article",
        extra={"used_playwright": True},
    )


def _fallback_title_from_url(url: str) -> str:
    return urlparse(url).netloc


def _site_name(url: str) -> str:
    host = urlparse(url).netloc
    return host.removeprefix("www.")


# ── PDF fetching (Marker → pymupdf4llm fallback) ─────────────────────────────

def _is_pdf_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(".pdf")


def _pdf_title_from_text(md_text: str, fallback: str) -> str:
    for line in md_text.split("\n"):
        stripped = line.strip()
        if re.match(r"^#{1,3}\s", stripped):
            title = stripped.lstrip("#").strip().strip("*")
            if title:
                return title
    return fallback


def _marker_convert(file_path: str) -> "tuple[Optional[str], List[Path]]":
    """
    Convert PDF to markdown using Marker (high-fidelity).
    Returns (markdown_text, list_of_extracted_image_paths) or (None, []).
    Images are saved to a temp dir that the caller must handle before cleanup.
    """
    if not config.MARKER_PATH:
        return None, []

    try:
        # Use a persistent temp dir (caller handles cleanup via image copying)
        tmp_dir = tempfile.mkdtemp(prefix="marker-")
        result = subprocess.run(
            [str(config.MARKER_PATH), file_path, "--output_dir", tmp_dir],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning("Marker failed (rc=%d): %s", result.returncode, result.stderr[:500])
            return None, []

        # Marker outputs a subdirectory with the markdown file + images
        md_files = list(Path(tmp_dir).rglob("*.md"))
        if not md_files:
            logger.warning("Marker produced no .md output")
            return None, []

        # Take the largest .md file (main output)
        md_file = max(md_files, key=lambda f: f.stat().st_size)
        md_text = md_file.read_text(encoding="utf-8")

        # Find all extracted images
        img_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}
        images = [
            p for p in Path(tmp_dir).rglob("*")
            if p.suffix.lower() in img_extensions and p.stat().st_size > 1000
        ]

        if md_text.strip():
            logger.info("Marker converted PDF: %d chars, %d images", len(md_text), len(images))
            # Log image filenames and all image references in the markdown for debugging
            img_names = [p.name for p in images]
            md_img_refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", md_text)
            html_img_refs = re.findall(r'<img\s[^>]*src="([^"]+)"', md_text)
            logger.info("Marker image files: %s", img_names)
            logger.info("Marker md image refs: %s", md_img_refs)
            if html_img_refs:
                logger.info("Marker html image refs: %s", html_img_refs)
            return md_text, images

    except subprocess.TimeoutExpired:
        logger.warning("Marker timed out on %s", file_path)
    except Exception as e:
        logger.warning("Marker error: %s", e)

    return None, []


def _pymupdf_convert(file_path: str) -> str:
    """Fallback: convert PDF using pymupdf4llm."""
    import pymupdf4llm
    return pymupdf4llm.to_markdown(file_path)


def fetch_pdf(file_path: str, filename: str = "") -> FetchedContent:
    """Extract markdown from a local PDF file. Tries Marker first, then pymupdf4llm."""
    import shutil

    path = Path(file_path)
    fallback_name = filename or path.stem
    converter = "pymupdf4llm"
    extracted_images: List[Path] = []

    # Tier 1: Marker (full-fidelity, with image extraction)
    md_text, marker_images = _marker_convert(file_path)
    if md_text:
        converter = "marker"
        extracted_images = marker_images
    else:
        # Tier 2: pymupdf4llm fallback (no image extraction)
        logger.info("Using pymupdf4llm fallback for %s", fallback_name)
        md_text = _pymupdf_convert(file_path)

    # Copy extracted images to vault attachments and rewrite markdown paths
    if extracted_images:
        attach_dir = config.VAULT_PATH / "Personal" / "5 attachments"
        attach_dir.mkdir(parents=True, exist_ok=True)

        for img_path in extracted_images:
            dest = attach_dir / img_path.name
            if not dest.exists():
                shutil.copy2(str(img_path), str(dest))
                logger.info("Copied PDF image: %s", img_path.name)

            name = img_path.name
            escaped = re.escape(name)

            # 1. Rewrite markdown image refs: ![alt](path/to/image.png) → ![[image.png]]
            md_text = re.sub(
                rf"!\[([^\]]*)\]\([^)]*{escaped}\)",
                f"![[{name}]]",
                md_text,
            )

            # 2. Rewrite HTML <img> tags that Marker sometimes produces
            md_text = re.sub(
                rf'<img\s[^>]*src="[^"]*{escaped}"[^>]*/?>',
                f"![[{name}]]",
                md_text,
                flags=re.IGNORECASE,
            )

            # 3. Also catch URL-encoded variants (spaces → %20 etc.)
            name_no_ext = img_path.stem
            ext = img_path.suffix
            escaped_stem = re.escape(name_no_ext)
            md_text = re.sub(
                rf"!\[([^\]]*)\]\([^)]*{escaped_stem}{re.escape(ext)}\)",
                f"![[{name}]]",
                md_text,
            )

        # Find orphan images — extracted by Marker but not referenced in markdown
        orphan_images = []
        for img_path in extracted_images:
            name = img_path.name
            if f"![[{name}]]" not in md_text:
                orphan_images.append(img_path)

        if orphan_images:
            logger.info("Found %d orphan images (extracted but unreferenced)", len(orphan_images))
            # Group orphans by page number and insert after the nearest same-page image
            for orphan in orphan_images:
                # Extract page number from Marker naming: _page_X_Picture_Y.jpeg
                page_match = re.search(r"_page_(\d+)_", orphan.name)
                if page_match:
                    page_num = page_match.group(1)
                    # Find the last ![[...page_X_...]] embed on the same page
                    pattern = rf"(!\[\[_page_{re.escape(page_num)}_[^\]]+\]\])"
                    matches = list(re.finditer(pattern, md_text))
                    if matches:
                        # Insert after the last same-page image
                        last = matches[-1]
                        insert_pos = last.end()
                        md_text = md_text[:insert_pos] + f"\n\n![[{orphan.name}]]" + md_text[insert_pos:]
                        logger.info("Inserted orphan image after same-page ref: %s", orphan.name)
                        continue
                # Fallback: append at the end
                md_text += f"\n\n![[{orphan.name}]]"
                logger.info("Appended orphan image at end: %s", orphan.name)

        # Log any remaining unrewritten image references for debugging
        remaining = re.findall(r"!\[[^\]]*\]\([^)]+\)", md_text)
        remaining_html = re.findall(r'<img\s[^>]*src="([^"]+)"', md_text)
        if remaining or remaining_html:
            logger.warning(
                "Unrewritten image refs after PDF rewrite: md=%s, html=%s",
                remaining[:5], remaining_html[:5],
            )

    # Clean up Marker temp dir (find the marker-* root)
    if extracted_images:
        for parent in extracted_images[0].parents:
            if parent.name.startswith("marker-"):
                shutil.rmtree(parent, ignore_errors=True)
                break

    title = _pdf_title_from_text(md_text, fallback_name)

    return FetchedContent(
        url="",
        title=title,
        text=md_text,
        site_name="PDF",
        content_type="pdf",
        extra={"filename": filename or path.name, "converter": converter},
    )


def fetch_pdf_url(url: str) -> FetchedContent:
    """Download a PDF from a URL and extract its content."""
    resp = requests.get(url, headers=HEADERS, timeout=30, stream=True)
    resp.raise_for_status()

    url_path = unquote(urlparse(url).path)
    filename = Path(url_path).name or "document.pdf"

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        for chunk in resp.iter_content(chunk_size=65536):
            tmp.write(chunk)
        tmp_path = tmp.name

    try:
        content = fetch_pdf(tmp_path, filename)
        content.url = url
        return content
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ── Public entry point ────────────────────────────────────────────────────────

def fetch_x_article_url(url: str) -> FetchedContent:
    """A bare x.com/i/article/<id> link: no tweet to look up, go straight to the browser."""
    if not _browser.is_logged_in():
        raise RuntimeError("X Articles need a login — run `python login.py`")
    text = _browser.fetch_x_article(url) or ""
    if not text.strip():
        raise RuntimeError("X returned no article text — the session may have expired, run `python login.py`")
    first = next((l.lstrip("#").strip() for l in text.splitlines() if l.strip()), "")
    title = f"X Article: {first[:60]}{'…' if len(first) > 60 else ''}"
    return FetchedContent(
        url=url,
        title=title,
        text=text,
        site_name="X / Twitter",
        content_type="tweet",
        extra={"original_url": url, "x_article_url": url, "browser_fetched": True},
    )


def fetch(url: str) -> FetchedContent:
    url = url.strip()
    if _X_ARTICLE_RE.match(url):
        return fetch_x_article_url(url)
    if is_tweet_url(url):
        return fetch_tweet(url)
    if _is_pdf_url(url):
        return fetch_pdf_url(url)
    return fetch_article(url)
