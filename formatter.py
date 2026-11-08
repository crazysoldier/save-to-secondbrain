"""
Convert FetchedContent into an Obsidian-compatible markdown file and save it.

Enhanced with AI-generated summary + analysis sections (matching the
OpenClaw second-brain-topic skill's "minimum content standard").
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import List, Optional

from analyzer import Analysis
from fetcher import FetchedContent
from images import download_images


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


def build_markdown(
    content: FetchedContent,
    tags: List[str],
    analysis: Optional[Analysis] = None,
) -> str:
    today = date.today().isoformat()

    # Merge AI-suggested tags with default tags (deduplicated)
    all_tags = list(tags)
    if analysis and analysis.tags:
        for t in analysis.tags:
            if t not in all_tags:
                all_tags.append(t)

    # ── Frontmatter ──────────────────────────────────────────────────────────
    tag_list = ", ".join(f'"{t}"' for t in all_tags)
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

    # Extra metadata
    converter = content.extra.get("converter")
    if converter:
        lines.append(f"converter: {converter}")
    if content.extra.get("used_playwright"):
        lines.append("extraction: playwright")

    lines.append("---")
    lines.append("")

    # ── Body ─────────────────────────────────────────────────────────────────

    if content.content_type == "tweet":
        media = content.extra.get("media_urls", [])
        for mu in media:
            lines.append(f"![]({mu})")
        if media:
            lines.append("")

    lines.append(f"# {content.title}")
    lines.append("")

    # Source link (matching OpenClaw skill format)
    if content.url:
        lines.append(f"[Source]({content.url})")
        lines.append("")

    # Author + date line
    meta_parts = []
    if content.author:
        meta_parts.append(f"**Author:** {content.author}")
    if content.extra.get("created_at"):
        meta_parts.append(f"**Published:** {content.extra['created_at']}")
    if meta_parts:
        lines.append(" · ".join(meta_parts))
        lines.append("")

    if content.description:
        lines.append(f"*{content.description}*")
        lines.append("")

    # ── Summary + Analysis (AI-generated) ────────────────────────────────────
    if analysis:
        if analysis.summary:
            lines.append("## Summary")
            lines.append("")
            lines.append(analysis.summary)
            lines.append("")
        if analysis.analysis:
            lines.append("## Analysis")
            lines.append("")
            lines.append(analysis.analysis)
            lines.append("")

    lines.append("---")
    lines.append("")

    # ── Full content ─────────────────────────────────────────────────────────

    if content.content_type == "tweet":
        x_article_url = content.extra.get("x_article_url", "")
        if x_article_url:
            browser_fetched = content.extra.get("browser_fetched", False)
            if browser_fetched:
                if content.text:
                    lines.append(content.text)
                    lines.append("")
            else:
                lines.append(f"> [!NOTE] X Article — preview only")
                lines.append(f"> [Read full article on X]({x_article_url})")
                lines.append("")
                if content.text:
                    for tl in content.text.splitlines():
                        lines.append(f"> {tl}" if tl else ">")
                    lines.append(
                        "> *(preview — run `python login.py` to enable full fetch)*"
                    )
                    lines.append("")
        else:
            # Regular tweet: quote-block the text
            tweet_lines = content.text.splitlines()
            for tl in tweet_lines:
                lines.append(f"> {tl}" if tl else ">")
            lines.append("")

    elif content.content_type == "pdf":
        # For PDFs: embed original + show converted markdown
        orig_filename = content.extra.get("filename", "")
        if orig_filename:
            lines.append(f"![[{orig_filename}]]")
            lines.append("")

        body = content.text
        if body:
            body_lines = body.split("\n")
            while body_lines and not body_lines[0].strip():
                body_lines.pop(0)
            if body_lines:
                first_clean = body_lines[0].lstrip("#").strip()
                if first_clean.lower() == content.title.lower():
                    body_lines.pop(0)
                    while body_lines and not body_lines[0].strip():
                        body_lines.pop(0)
            lines.append("\n".join(body_lines))
            lines.append("")
    else:
        # Article: full text body
        if content.text:
            lines.append(content.text)
            lines.append("")

    return "\n".join(lines)


def save(
    content: FetchedContent,
    vault_path: Path,
    folder_name: str,
    tags: List[str],
    analysis: Optional[Analysis] = None,
) -> Path:
    save_dir = vault_path / folder_name
    save_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    stem = f"{today} - {_slugify(content.title)}"
    filepath = _unique_path(save_dir, stem)

    md = build_markdown(content, tags, analysis)

    # Download remote images to vault attachments folder and rewrite paths
    md = download_images(
        md, vault_path,
        attachments_folder="Personal/5 attachments",
        source_url=content.url,
    )

    filepath.write_text(md, encoding="utf-8")
    return filepath
