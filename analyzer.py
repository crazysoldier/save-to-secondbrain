"""
AI-powered summary and analysis generation using Claude API.

Generates two outputs for every ingested resource:
  - Summary: what it says (factual, concise)
  - Analysis: why it matters, how it connects, strategic implication
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import config

logger = logging.getLogger(__name__)


@dataclass
class Analysis:
    summary: str
    analysis: str
    tags: List[str]


_SYSTEM_PROMPT = """\
You are a knowledge management assistant. Given an article or document, produce:

1. **Summary** (2-4 sentences): What does this say? Key facts, claims, findings.
2. **Analysis** (2-4 sentences): Why does it matter? How does it connect to broader themes? What's the strategic implication?
3. **Tags** (3-6 tags): Lowercase, no hashes, comma-separated. Focus on topics, domains, and key entities.

The reader may add a note saying why they saved it or what they want to know. Keep the summary
about the document itself; aim the analysis at the reader's note and answer any question in it.

Respond in this exact format:
SUMMARY: <your summary>
ANALYSIS: <your analysis>
TAGS: tag1, tag2, tag3
"""


def _parse_response(text: str) -> Analysis:
    """Parse the structured LLM response into an Analysis object."""
    summary = ""
    analysis = ""
    tags: List[str] = []

    for line in text.strip().split("\n"):
        line = line.strip()
        if line.upper().startswith("SUMMARY:"):
            summary = line.split(":", 1)[1].strip()
        elif line.upper().startswith("ANALYSIS:"):
            analysis = line.split(":", 1)[1].strip()
        elif line.upper().startswith("TAGS:"):
            raw_tags = line.split(":", 1)[1].strip()
            tags = [
                t.strip().lower().lstrip("#").replace(" ", "-")
                for t in raw_tags.split(",") if t.strip()
            ]

    return Analysis(summary=summary, analysis=analysis, tags=tags)


def analyze(
    title: str, text: str, content_type: str = "article", comment: Optional[str] = None
) -> Optional[Analysis]:
    """
    Generate summary + analysis for a piece of content using Claude.
    Returns None if API key is not configured or call fails.
    """
    if not config.ANTHROPIC_API_KEY:
        logger.info("No ANTHROPIC_API_KEY — skipping AI analysis")
        return None

    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed — skipping AI analysis")
        return None

    # Truncate very long texts to stay within token limits
    max_chars = 12_000
    truncated = text[:max_chars]
    if len(text) > max_chars:
        truncated += "\n\n[... truncated ...]"

    user_msg = f"Title: {title}\nType: {content_type}\n"
    if comment:
        user_msg += f"Reader's note: {comment}\n"
    user_msg += f"\n---\n\n{truncated}"

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-5-5",
            max_tokens=4000,
            output_config={"effort": "low"},
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
            # On a safety decline, let the API retry on a fallback model.
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
            extra_body={"fallbacks": "default"},
        )
        if response.stop_reason == "refusal":
            logger.warning("Claude declined to analyze %r", title)
            return None
        result_text = next(b.text for b in response.content if b.type == "text")
        return _parse_response(result_text)
    except Exception as e:
        logger.error("Claude API call failed: %s", e)
        return None
