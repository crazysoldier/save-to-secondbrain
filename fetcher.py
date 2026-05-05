"""
Content fetching for articles, websites, and X/Twitter posts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List
from urllib.parse import urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup

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
    content_type: str = "article"  # "article" | "tweet"
    extra: Dict = field(default_factory=dict)


# ── URL helpers ───────────────────────────────────────────────────────────────

# Matches x.com and twitter.com post URLs
_TWITTER_RE = re.compile(
    r"https?://(?:www\.)?(twitter\.com|x\.com)/(\w+)/status/(\d+)",
    re.IGNORECASE,
)

# FxEmbed v2 API — https://github.com/FxEmbed/FxEmbed
_FXEMBED_API = "https://api.fxtwitter.com/2/status/{tweet_id}"
_FXEMBED_WEB = "https://fxtwitter.com/{username}/status/{tweet_id}"


def is_tweet_url(url: str) -> bool:
    return bool(_TWITTER_RE.match(url))


def _parse_tweet_url(url: str) -> "tuple[str, str]":
    """Return (username, tweet_id) from a tweet URL."""
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

    # v2 response: top-level key is "status" (v2) or "tweet" (legacy)
    post = data.get("tweet") or data.get("status") or {}
    author = post.get("author", {})

    text = post.get("text", "").strip()
    author_name = author.get("name", "")
    author_handle = author.get("screen_name", username)
    created_at = post.get("created_at", "")

    # v2 media: post.media.all[] — each item has .type and .url / .thumbnail_url
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

    if not text and article:
        title = article.get("title", "") or f"@{author_handle} article"
        x_article_url = f"https://x.com/i/article/{article['id']}" if article.get("id") else ""

        cover = (article.get("cover_media") or {}).get("media_info", {})
        cover_url = cover.get("url", "") or cover.get("original_img_url", "")
        if cover_url and cover_url not in media_urls:
            media_urls.insert(0, cover_url)

        # Try to fetch full content via saved browser session
        if x_article_url:
            import logging as _logging
            import browser as _browser
            _log = _logging.getLogger(__name__)
            if _browser.is_logged_in():
                try:
                    full_text = _browser.fetch_x_article(x_article_url)
                    if full_text and len(full_text) > len(article.get("preview_text", "")):
                        # Drop duplicate title line at the top of the body
                        ft_lines = full_text.split("\n")
                        if ft_lines and title:
                            clean_first = ft_lines[0].lstrip("#").strip()
                            if clean_first.lower() == title.lower():
                                full_text = "\n".join(ft_lines[1:]).lstrip("\n")
                        text = full_text
                        browser_fetched = True
                        _log.info("Browser fetch OK: %d chars", len(full_text))
                    else:
                        _log.warning("Browser fetch returned too little text (%d chars), using preview", len(full_text))
                except Exception as e:
                    _log.warning("Browser fetch failed: %s — falling back to preview", e)

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


# ── Article fetching ──────────────────────────────────────────────────────────

def fetch_article(url: str) -> FetchedContent:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    html = resp.text

    # trafilatura for main content
    text = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        no_fallback=False,
        favor_precision=True,
    ) or ""

    # Metadata via trafilatura
    meta = trafilatura.extract_metadata(html, default_url=url)

    title = (meta.title if meta and meta.title else "") or _fallback_title(html, url)
    author = (meta.author if meta and meta.author else "") or ""
    description = (meta.description if meta and meta.description else "") or ""
    site_name = (meta.sitename if meta and meta.sitename else "") or _site_name(url)

    return FetchedContent(
        url=url,
        title=title,
        text=text,
        author=author,
        site_name=site_name,
        description=description,
        content_type="article",
    )


def _fallback_title(html: str, url: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("title")
    if tag and tag.string:
        return tag.string.strip()
    return urlparse(url).netloc


def _site_name(url: str) -> str:
    host = urlparse(url).netloc
    return host.removeprefix("www.")


# ── Public entry point ────────────────────────────────────────────────────────

def fetch(url: str) -> FetchedContent:
    url = url.strip()
    if is_tweet_url(url):
        return fetch_tweet(url)
    return fetch_article(url)
