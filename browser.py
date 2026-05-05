"""
Fetches login-gated X Article content using cookies from your real browser.

Run login.py once to extract cookies from Brave/Chrome.
The bot uses those cookies in a headless Chromium with bot-detection bypassed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

COOKIES_FILE = Path(__file__).parent / "session_cookies.json"


def is_logged_in() -> bool:
    return COOKIES_FILE.exists() and COOKIES_FILE.stat().st_size > 10


def _inner_text_to_markdown(raw: str) -> str:
    """
    Convert the inner_text() output of twitterArticleRichTextView to markdown.

    Structure rule: a line is a heading (##) if it is:
      - surrounded by blank lines (or at start/end of content)
      - short enough to be a title (< 80 chars)
      - does not end with a sentence-ending period or comma
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
            (prev_blank and next_blank)       # surrounded by blanks
            or (prev_blank and len(stripped) < 50)   # short line after a gap
            or (next_blank and len(stripped) < 50)   # short line before a gap
        )

        if is_heading:
            out.append(f"## {stripped}")
        else:
            out.append(stripped)

    # Collapse 3+ consecutive blank lines to 2
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Strip X boilerplate footer — match on stripped line content so heading
    # prefixes (##) and surrounding whitespace don't cause misses
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
    Load an X Article with your saved cookies and return formatted markdown.
    Runs synchronously — safe to call from a thread pool.
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

        # Extract from the article rich-text container directly
        try:
            raw = page.locator(
                "[data-testid='twitterArticleRichTextView']"
            ).inner_text(timeout=10_000)
        except Exception:
            # Fallback: full page inner_text stripped of nav noise
            raw = page.locator("main").inner_text(timeout=5_000)

        browser.close()

    if not raw or "JavaScript is disabled" in raw:
        return ""

    return _inner_text_to_markdown(raw)
