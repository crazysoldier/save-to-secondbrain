"""
Download images from markdown content and rewrite URLs to local Obsidian paths.

Finds all ![alt](url) patterns, downloads each image to the vault's
attachments folder, and rewrites to ![[local-filename]] for offline access.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import urlparse, unquote

import requests

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
TIMEOUT = 20

# Match markdown images: ![alt](url) — but not Obsidian embeds ![[...]]
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

# Common image extensions
_IMG_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif", ".bmp"}


def _guess_extension(url: str, content_type: str = "") -> str:
    """Guess file extension from URL path or Content-Type header."""
    # Try URL path first
    path = urlparse(url).path.lower()
    for ext in _IMG_EXTENSIONS:
        if path.endswith(ext):
            return ext

    # Try Content-Type
    ct = content_type.lower()
    if "jpeg" in ct or "jpg" in ct:
        return ".jpg"
    if "png" in ct:
        return ".png"
    if "gif" in ct:
        return ".gif"
    if "webp" in ct:
        return ".webp"
    if "svg" in ct:
        return ".svg"

    return ".png"  # safe default


def _safe_filename(url: str, alt: str, ext: str) -> str:
    """Generate a short, unique-ish filename from URL and alt text."""
    # Use alt text if available, otherwise derive from URL
    if alt and len(alt) > 3:
        base = re.sub(r"[^\w\s\-]", "", alt)[:50].strip()
        base = re.sub(r"\s+", "-", base)
    else:
        path = unquote(urlparse(url).path)
        base = Path(path).stem[:50] or "image"

    # Append short hash of URL for uniqueness
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    return f"{base}-{url_hash}{ext}"


def download_images(
    markdown: str,
    vault_path: Path,
    attachments_folder: str = "attachments",
    source_url: str = "",
) -> str:
    """
    Find all remote images in markdown, download them to vault attachments,
    and rewrite the markdown to use Obsidian ![[filename]] embeds.

    source_url: the original article URL, used to resolve relative image paths.
    Returns the updated markdown text.
    """
    attach_dir = vault_path / attachments_folder
    attach_dir.mkdir(parents=True, exist_ok=True)

    # Pre-compute base URL for resolving relative paths
    _base_url = ""
    if source_url:
        parsed = urlparse(source_url)
        _base_url = f"{parsed.scheme}://{parsed.netloc}"

    def _replace_image(match: re.Match) -> str:
        alt = match.group(1)
        url = match.group(2).strip()

        # Skip data URIs and already-local paths
        if url.startswith("data:") or url.startswith("file:"):
            return match.group(0)

        # Skip Obsidian-style embeds (shouldn't match but just in case)
        if url.startswith("[["):
            return match.group(0)

        # Resolve relative URLs ("//host/x" is protocol-relative, e.g. Wikipedia)
        if url.startswith("//"):
            url = f"{urlparse(source_url).scheme or 'https'}:{url}"
        elif url.startswith("/") and _base_url:
            url = _base_url + url
        elif not url.startswith("http"):
            # Relative path without leading slash — skip, can't resolve
            logger.debug("Skipping unresolvable relative image: %s", url)
            return ""

        # Skip tracking pixels, avatars, icons, and other non-content images
        skip_patterns = [
            "pixel", "beacon", "tracking", "spacer", "1x1",
            "avatar", "profile", "gravatar", "icon", "favicon",
            "logo", "badge", "emoji", "smiley",
        ]
        if any(p in url.lower() for p in skip_patterns):
            logger.debug("Skipping non-content image: %s", url)
            return ""

        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
            resp.raise_for_status()

            # Check it's actually an image
            ct = resp.headers.get("Content-Type", "")
            if ct and "image" not in ct and "octet-stream" not in ct:
                logger.debug("Skipping non-image content-type %s: %s", ct, url)
                return match.group(0)

            # Check size — skip if too big (>20MB) or too small (<5KB = likely icon/avatar)
            size = int(resp.headers.get("Content-Length", 0))
            if size > 20 * 1024 * 1024:
                logger.warning("Skipping oversized image (%d bytes): %s", size, url)
                return match.group(0)
            if 0 < size < 5_000:
                logger.debug("Skipping tiny image (%d bytes, likely icon): %s", size, url)
                return ""

            ext = _guess_extension(url, ct)
            filename = _safe_filename(url, alt, ext)
            filepath = attach_dir / filename

            # Don't re-download if already exists
            if not filepath.exists():
                with open(filepath, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=65536):
                        f.write(chunk)
                logger.info("Downloaded image: %s → %s", url[:80], filename)
            else:
                logger.debug("Image already exists: %s", filename)

            # Return Obsidian embed format
            return f"![[{filename}]]"

        except Exception as e:
            logger.warning("Failed to download image %s: %s", url[:80], e)
            # Keep original remote URL as fallback
            return match.group(0)

    return _IMG_RE.sub(_replace_image, markdown)
