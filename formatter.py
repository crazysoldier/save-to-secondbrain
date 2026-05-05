"""
Convert FetchedContent into an Obsidian-compatible markdown file and save it.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import List

from fetcher import FetchedContent


def _slugify(text: str, max_len: int = 80) -> str:
    """Turn a title into a safe filename."""
    text = re.sub(r"[^\w\s\-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    text = text[:max_len].rstrip()
    return text


def _unique_path(folder: Path, stem: str, suffix: str = ".md") -> Path:
    path = folder / f"{stem}{suffix}"
    counter = 1
    while path.exists():
        path = folder / f"{stem} ({counter}){suffix}"
        counter += 1
    return path


def _yaml_str(value: str) -> str:
    """Wrap a string in YAML quotes if it contains special chars."""
    if any(c in value for c in (':', '"', "'", '#', '[', ']', '{', '}')):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def build_markdown(content: FetchedContent, tags: List[str]) -> str:
    today = date.today().isoformat()

    # ── Frontmatter ──────────────────────────────────────────────────────────
    tag_list = ", ".join(f'"{t}"' for t in tags)
    lines: List[str] = ["---"]
    lines.append(f"title: {_yaml_str(content.title)}")
    lines.append(f"url: {content.url}")
    lines.append(f"date: {today}")
    if content.author:
        lines.append(f"author: {_yaml_str(content.author)}")
    if content.site_name:
        lines.append(f"source: {_yaml_str(content.site_name)}")
    lines.append(f"type: {content.content_type}")
    lines.append(f"tags: [{tag_list}]")
    lines.append("---")
    lines.append("")

    # ── Body ─────────────────────────────────────────────────────────────────

    if content.content_type == "tweet":
        media = content.extra.get("media_urls", [])
        x_article_url = content.extra.get("x_article_url", "")

        # Media before the title
        for mu in media:
            lines.append(f"![]({mu})")
        if media:
            lines.append("")

    lines.append(f"# {content.title}")
    lines.append("")

    if content.description:
        lines.append(f"*{content.description}*")
        lines.append("")

    if content.content_type == "tweet":
        if x_article_url:
            browser_fetched = content.extra.get("browser_fetched", False)
            if browser_fetched:
                if content.text:
                    lines.append(content.text)
                    lines.append("")
            else:
                # Preview only — no browser session available
                lines.append(f"> [!NOTE] X Article — preview only")
                lines.append(f"> [Read full article on X]({x_article_url})")
                lines.append("")
                if content.text:
                    for tl in content.text.splitlines():
                        lines.append(f"> {tl}" if tl else ">")
                    lines.append("> *(preview — run `python login.py` to enable full fetch)*")
                    lines.append("")
        else:
            # Regular tweet: quote-block the text
            tweet_lines = content.text.splitlines()
            for tl in tweet_lines:
                lines.append(f"> {tl}" if tl else ">")
            lines.append("")

        if content.extra.get("created_at"):
            lines.append(f"*Posted: {content.extra['created_at']}*")
            lines.append("")
    else:
        # Article: plain text body
        if content.text:
            lines.append(content.text)
            lines.append("")

    return "\n".join(lines)


def save(content: FetchedContent, vault_path: Path, folder_name: str, tags: List[str]) -> Path:
    save_dir = vault_path / folder_name
    save_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    stem = f"{today} - {_slugify(content.title)}"
    filepath = _unique_path(save_dir, stem)

    md = build_markdown(content, tags)
    filepath.write_text(md, encoding="utf-8")
    return filepath
