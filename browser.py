"""
Browser-based content extraction using Playwright.

Two modes:
  1. General page fetch — headless Chromium, no cookies needed
  2. X Article fetch — uses saved session cookies for login-gated content

Extracts HTML and converts to markdown via markdownify to preserve
formatting (bold, italic, headings, links, images).

Run login.py once to extract cookies from Brave/Chrome for X Articles.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from markdownify import markdownify as md

logger = logging.getLogger(__name__)

COOKIES_FILE = Path(__file__).parent / "session_cookies.json"

# Selectors tried in order for general page extraction
_CONTENT_SELECTORS = [
    "article",
    "[role='main'] article",
    "main",
    "[role='main']",
    "#content",
    ".post-content",
    ".article-content",
    ".entry-content",
    "body",
]


def is_logged_in() -> bool:
    return COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 10


def _html_to_markdown(html: str) -> str:
    """Convert HTML to clean markdown preserving formatting and images."""
    # Remove TOC / table-of-contents blocks before conversion
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    # Remove elements that are typically TOCs or site chrome
    for el in soup.find_all(["nav", "footer", "header", "aside", "form",
                             "button", "input", "select", "textarea",
                             "iframe", "noscript", "script", "style"]):
        el.decompose()
    # Remove TOC-like containers (common patterns)
    for el in soup.find_all(attrs={"class": re.compile(r"toc|table.of.contents|contents-list", re.I)}):
        el.decompose()
    for el in soup.find_all(attrs={"id": re.compile(r"toc|table.of.contents", re.I)}):
        el.decompose()
    # Remove "Contents" sections that are just anchor link lists
    for el in soup.find_all(["details", "summary"]):
        if el.get_text(strip=True).lower().startswith("contents"):
            el.decompose()
    # Remove copy-to-clipboard duplicates and hidden elements
    for el in soup.find_all(attrs={"class": re.compile(r"copy|clipboard", re.I)}):
        el.decompose()
    for el in soup.find_all(attrs={"aria-hidden": "true"}):
        el.decompose()
    for el in soup.find_all(attrs={"style": re.compile(r"display:\s*none|visibility:\s*hidden", re.I)}):
        el.decompose()

    text = md(
        str(soup),
        heading_style="ATX",
        bullets="-",
    )
    # Collapse 3+ blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Deduplicate consecutive identical code blocks
    text = re.sub(
        r"(```[^\n]*\n(?:.*?\n)*?```)\s*\1",
        r"\1",
        text,
        flags=re.DOTALL,
    )
    # Remove common nav/cookie noise lines
    lines = text.split("\n")
    skip_patterns = re.compile(
        r"^\s*(Cookie|Accept all|Reject all|Subscribe|Sign [Uu]p|Log [Ii]n|"
        r"Share this|Tweet|Facebook|LinkedIn|Pinterest|Reddit|"
        r"Privacy Policy|Terms of Service|©|All rights reserved)\s*$",
        re.IGNORECASE,
    )
    cleaned = [l for l in lines if not skip_patterns.match(l)]
    return "\n".join(cleaned).strip()


def fetch_page(url: str, timeout_ms: int = 30_000) -> str:
    """
    Fetch any web page using headless Playwright.
    Returns markdown with formatting preserved (bold, italic, headings, images).
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(2_000)

            # Try content selectors in priority order — use inner_html for formatting
            html = ""
            best_len = 0
            for selector in _CONTENT_SELECTORS:
                try:
                    locator = page.locator(selector).first
                    if locator.count() > 0:
                        candidate = locator.inner_html(timeout=5_000)
                        if candidate and len(candidate) > best_len:
                            html = candidate
                            best_len = len(candidate)
                            if best_len > 2000:
                                break
                except Exception:
                    continue

            # Scroll down for lazy-loaded content
            if best_len < 3000:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1_500)
                for selector in _CONTENT_SELECTORS[:3]:
                    try:
                        locator = page.locator(selector).first
                        if locator.count() > 0:
                            candidate = locator.inner_html(timeout=5_000)
                            if candidate and len(candidate) > best_len:
                                html = candidate
                                best_len = len(candidate)
                                break
                    except Exception:
                        continue

        except Exception as e:
            logger.warning("Playwright navigation failed for %s: %s", url, e)
            html = ""
        finally:
            browser.close()

    if not html:
        return ""

    return _html_to_markdown(html)


def fetch_page_metadata(url: str) -> dict:
    """
    Fetch page metadata (title, author, description, site_name) via Playwright.
    Returns a dict with available fields.
    """
    from playwright.sync_api import sync_playwright

    meta = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            page.wait_for_timeout(1_000)

            meta = page.evaluate("""() => {
                const getMeta = (names) => {
                    for (const name of names) {
                        const el = document.querySelector(
                            `meta[property="${name}"], meta[name="${name}"]`
                        );
                        if (el) return el.getAttribute('content') || '';
                    }
                    return '';
                };
                return {
                    title: document.title || '',
                    og_title: getMeta(['og:title', 'twitter:title']),
                    author: getMeta(['author', 'article:author', 'twitter:creator']),
                    description: getMeta(['description', 'og:description', 'twitter:description']),
                    site_name: getMeta(['og:site_name']),
                };
            }""")
        except Exception as e:
            logger.warning("Metadata extraction failed for %s: %s", url, e)
        finally:
            browser.close()

    return meta


def _inner_text_to_markdown(raw: str) -> str:
    """
    Convert the inner_text() output of twitterArticleRichTextView to markdown.
    Used specifically for X Articles where inner_html is messy.
    """
    lines = raw.split("\n")
    out: list[str] = []
    n = len(lines)

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue

        prev_blank = (i == 0) or (not lines[i - 1].strip())
        next_blank = (i == n - 1) or (not lines[i + 1].strip())

        looks_like_title = (
            len(stripped) < 70
            and not stripped.endswith(".")
            and not stripped.endswith(",")
            and stripped not in ("—", "-", "•")
        )
        is_heading = looks_like_title and (
            (prev_blank and next_blank)
            or (prev_blank and len(stripped) < 50)
            or (next_blank and len(stripped) < 50)
        )

        if is_heading:
            out.append(f"## {stripped}")
        else:
            out.append(stripped)

    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)

    _FOOTER_MARKERS = {
        "Want to publish your own Article?",
        "Upgrade to Premium",
        "Upgrade to Premium+",
    }
    result_lines = text.split("\n")
    for i, line in enumerate(result_lines):
        clean = line.strip().lstrip("#").strip()
        if clean in _FOOTER_MARKERS:
            text = "\n".join(result_lines[:i]).rstrip()
            break

    return text.strip()


def fetch_x_article(article_url: str) -> str:
    """
    Load an X Article with saved cookies and return formatted markdown.
    Tries inner_html + markdownify first, falls back to inner_text heuristic.
    """
    from playwright.sync_api import sync_playwright

    cookies = json.loads(COOKIES_FILE.read_text())

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        context.add_cookies(cookies)

        page = context.new_page()
        page.goto(article_url, wait_until="load", timeout=30_000)
        page.wait_for_timeout(3_000)

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(1_500)

        result = ""
        # Try HTML extraction first for rich formatting
        try:
            html = page.locator(
                "[data-testid='twitterArticleRichTextView']"
            ).inner_html(timeout=10_000)
            if html:
                result = _html_to_markdown(html)
        except Exception:
            pass

        # Fall back to inner_text heuristic
        if not result or len(result) < 200:
            try:
                raw = page.locator(
                    "[data-testid='twitterArticleRichTextView']"
                ).inner_text(timeout=10_000)
                if raw:
                    fallback = _inner_text_to_markdown(raw)
                    if len(fallback) > len(result):
                        result = fallback
            except Exception:
                try:
                    raw = page.locator("main").inner_text(timeout=5_000)
                    if raw:
                        result = _inner_text_to_markdown(raw)
                except Exception:
                    pass

        browser.close()

    if not result or "JavaScript is disabled" in result:
        return ""

    return result
