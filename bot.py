"""
Telegram bot: receive a URL or PDF, fetch content, generate AI summary/analysis,
save as Obsidian markdown with AI summary/analysis.

Usage:
    python bot.py
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from pathlib import Path
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import analyzer
import config
import fetcher
import formatter

logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)
# httpx logs every getUpdates poll at INFO, including the bot token in the URL.
logging.getLogger("httpx").setLevel(logging.WARNING)

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


# ── Auth guard ────────────────────────────────────────────────────────────────

def _is_allowed(update: Update) -> bool:
    if config.ALLOWED_USER_ID is None:
        return True
    return update.effective_user is not None and update.effective_user.id == config.ALLOWED_USER_ID


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    vault = config.VAULT_PATH / config.SAVE_FOLDER
    features = [
        "Extract articles with full formatting + images (Playwright)",
        "Fetch tweets & X Articles (with browser session)",
        "Convert PDFs (Marker or pymupdf4llm)",
        "AI summary + analysis via Claude",
        "Your comment next to a link is saved and steers the analysis",
        "Wiki integration (_Meta/log + index)",
    ]
    feature_list = "\n".join(f"  • {f}" for f in features)
    await update.message.reply_text(
        f"Send me any URL or PDF and I'll save it to your second brain.\n\n"
        f"Vault: `{vault}`\n\n"
        f"Features:\n{feature_list}",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        return
    await update.message.reply_text(
        "*Commands*\n"
        "/start — welcome message\n"
        "/help  — this message\n\n"
        "*Just send a URL* and I'll:\n"
        "• Extract the full article (Playwright for JS-heavy sites)\n"
        "• For x.com / twitter.com links: fetch via fxtwitter + browser\n"
        "• PDF links or file attachments: convert with Marker\n"
        "• Generate AI summary + analysis\n"
        "• Save a clean .md file to your vault\n"
        "• Download images for offline access\n\n"
        "Add a comment next to the link (or as a PDF caption): it's saved "
        "under *My note* and the analysis answers it.",
        parse_mode="Markdown",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await update.message.reply_text("⛔ Not authorised.")
        return

    text = update.message.text or update.message.caption or ""
    urls = URL_RE.findall(text)

    if not urls:
        await update.message.reply_text("Send me a URL and I'll save it.")
        return

    # Whatever else was typed alongside the link(s) is Max's own note.
    comment = " ".join(URL_RE.sub("", text).split()) or None

    for url in urls:
        await _process_url(update, url, comment)


async def _process_url(update: Update, url: str, comment: Optional[str] = None) -> None:
    status_msg = await update.message.reply_text(
        f"⏳ Fetching `{url}` …", parse_mode="Markdown"
    )

    # 1. Fetch content
    try:
        content = await asyncio.to_thread(fetcher.fetch, url)
    except Exception as exc:
        logger.exception("Fetch failed for %s", url)
        await status_msg.edit_text(
            f"❌ Could not fetch URL:\n`{exc}`", parse_mode="Markdown"
        )
        return

    # 2. AI summary + analysis
    await status_msg.edit_text(
        f"🧠 Analyzing `{content.title[:50]}` …", parse_mode="Markdown"
    )
    try:
        ai_result = await asyncio.to_thread(
            analyzer.analyze, content.title, content.text, content.content_type, comment
        )
    except Exception as exc:
        logger.warning("Analysis failed: %s — saving without analysis", exc)
        ai_result = None

    # 3. Merge AI tags with defaults
    all_tags = list(config.DEFAULT_TAGS)
    if ai_result and ai_result.tags:
        for t in ai_result.tags:
            if t not in all_tags:
                all_tags.append(t)

    # 4. Save to Obsidian
    try:
        saved_path = formatter.save(
            content,
            vault_path=config.VAULT_PATH,
            folder_name=config.SAVE_FOLDER,
            tags=all_tags,
            analysis=ai_result,
            comment=comment,
        )
    except Exception as exc:
        logger.exception("Save failed for %s", url)
        await status_msg.edit_text(
            f"❌ Could not save note:\n`{exc}`", parse_mode="Markdown"
        )
        return

    # 5. Report back
    rel = saved_path.relative_to(config.VAULT_PATH)
    icon = "🐦" if content.content_type == "tweet" else "📑" if content.content_type == "pdf" else "📄"
    extraction_note = ""
    if content.extra.get("used_playwright"):
        extraction_note = "\n_Extracted via Playwright_"
    if content.extra.get("converter") == "marker":
        extraction_note = "\n_Converted via Marker_"

    reply_parts = [
        f"{icon} *Saved:* `{rel}`",
    ]

    if ai_result and ai_result.summary:
        reply_parts.append(f"\n*Summary:* {ai_result.summary}")
    if ai_result and ai_result.analysis:
        reply_parts.append(f"\n*Analysis:* {ai_result.analysis}")

    if extraction_note:
        reply_parts.append(extraction_note)

    await status_msg.edit_text(
        "\n".join(reply_parts),
        parse_mode="Markdown",
    )
    logger.info("Saved %s → %s", url, saved_path)


# ── Document handler ─────────────────────────────────────────────────────────

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle PDF (and other document) file attachments."""
    if not _is_allowed(update):
        await update.message.reply_text("⛔ Not authorised.")
        return

    doc = update.message.document
    if not doc:
        return

    filename = doc.file_name or "document"
    comment = (update.message.caption or "").strip() or None
    status_msg = await update.message.reply_text(
        f"⏳ Processing `{filename}` …", parse_mode="Markdown"
    )

    # Download file to a temp location
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        suffix = Path(filename).suffix or ".pdf"
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp.close()
        await tg_file.download_to_drive(tmp.name)
    except Exception as exc:
        logger.exception("Download failed for %s", filename)
        await status_msg.edit_text(
            f"❌ Could not download file:\n`{exc}`", parse_mode="Markdown"
        )
        return

    # 1. Copy original PDF to vault before processing
    save_dir = config.VAULT_PATH / config.SAVE_FOLDER
    save_dir.mkdir(parents=True, exist_ok=True)
    pdf_dest = save_dir / filename
    if not pdf_dest.exists():
        import shutil
        try:
            shutil.copy2(tmp.name, str(pdf_dest))
            logger.info("Saved original PDF: %s", pdf_dest)
        except Exception as exc:
            logger.warning("Could not save original PDF: %s", exc)

    # 2. Extract content
    try:
        content = await asyncio.to_thread(fetcher.fetch_pdf, tmp.name, filename)
    except Exception as exc:
        logger.exception("PDF extraction failed for %s", filename)
        await status_msg.edit_text(
            f"❌ Could not extract PDF:\n`{exc}`", parse_mode="Markdown"
        )
        return
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    # 3. AI summary + analysis
    await status_msg.edit_text(
        f"🧠 Analyzing `{content.title[:50]}` …", parse_mode="Markdown"
    )
    try:
        ai_result = await asyncio.to_thread(
            analyzer.analyze, content.title, content.text, content.content_type, comment
        )
    except Exception as exc:
        logger.warning("Analysis failed: %s", exc)
        ai_result = None

    # 4. Merge tags
    all_tags = list(config.DEFAULT_TAGS)
    if ai_result and ai_result.tags:
        for t in ai_result.tags:
            if t not in all_tags:
                all_tags.append(t)

    # 5. Save markdown companion note
    try:
        saved_path = formatter.save(
            content,
            vault_path=config.VAULT_PATH,
            folder_name=config.SAVE_FOLDER,
            tags=all_tags,
            analysis=ai_result,
            comment=comment,
        )
    except Exception as exc:
        logger.exception("Save failed for %s", filename)
        await status_msg.edit_text(
            f"❌ Could not save note:\n`{exc}`", parse_mode="Markdown"
        )
        return

    # 6. Report back
    rel = saved_path.relative_to(config.VAULT_PATH)
    converter = content.extra.get("converter", "unknown")
    reply_parts = [
        f"📑 *Saved:* `{rel}`",
        f"_Original PDF + markdown companion ({converter})_",
    ]
    if ai_result and ai_result.summary:
        reply_parts.append(f"\n*Summary:* {ai_result.summary}")
    if ai_result and ai_result.analysis:
        reply_parts.append(f"\n*Analysis:* {ai_result.analysis}")

    await status_msg.edit_text(
        "\n".join(reply_parts),
        parse_mode="Markdown",
    )
    logger.info("Saved PDF %s → %s", filename, saved_path)


# ── Entry point ───────────────────────────────────────────────────────────────

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception", exc_info=context.error)


async def post_init(app: Application) -> None:
    me = await app.bot.get_me()
    logger.info("✅ Logged in as @%s (id=%s)", me.username, me.id)
    logger.info("   Allowed user ID : %s", config.ALLOWED_USER_ID)
    logger.info("   Vault            : %s/%s", config.VAULT_PATH, config.SAVE_FOLDER)
    logger.info("   Marker           : %s", config.MARKER_PATH or "not configured (using pymupdf4llm)")
    logger.info("   Claude API       : %s", "configured" if config.ANTHROPIC_API_KEY else "not configured")


def main() -> None:
    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    app.add_error_handler(on_error)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, handle_message))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
