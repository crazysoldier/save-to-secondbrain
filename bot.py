"""
Telegram bot: receive a URL, fetch its content, save as Obsidian markdown.

Usage:
    python bot.py
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import fetcher
import formatter

logging.basicConfig(
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

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
    await update.message.reply_text(
        "👋 Send me any URL and I'll save it to your second brain.\n\n"
        f"Vault folder: `{vault}`",
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
        "• Extract the article text\n"
        "• For x.com / twitter.com links: fetch via fxtwitter\n"
        "• Save a clean .md file to your vault",
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

    for url in urls:
        await _process_url(update, url)


async def _process_url(update: Update, url: str) -> None:
    status_msg = await update.message.reply_text(f"⏳ Fetching `{url}` …", parse_mode="Markdown")

    try:
        # Run in a thread — sync_playwright needs its own event loop
        content = await asyncio.to_thread(fetcher.fetch, url)
    except Exception as exc:
        logger.exception("Fetch failed for %s", url)
        await status_msg.edit_text(f"❌ Could not fetch URL:\n`{exc}`", parse_mode="Markdown")
        return

    try:
        saved_path = formatter.save(
            content,
            vault_path=config.VAULT_PATH,
            folder_name=config.SAVE_FOLDER,
            tags=config.DEFAULT_TAGS,
        )
    except Exception as exc:
        logger.exception("Save failed for %s", url)
        await status_msg.edit_text(f"❌ Could not save note:\n`{exc}`", parse_mode="Markdown")
        return

    rel = saved_path.relative_to(config.VAULT_PATH)
    icon = "🐦" if content.content_type == "tweet" else "📄"
    await status_msg.edit_text(
        f"{icon} Saved!\n\n"
        f"*{content.title}*\n"
        f"`{rel}`",
        parse_mode="Markdown",
    )
    logger.info("Saved %s → %s", url, saved_path)


# ── Entry point ───────────────────────────────────────────────────────────────

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Unhandled exception", exc_info=context.error)


async def post_init(app: Application) -> None:
    me = await app.bot.get_me()
    logger.info("✅ Logged in as @%s (id=%s)", me.username, me.id)
    logger.info("   Allowed user ID : %s", config.ALLOWED_USER_ID)
    logger.info("   Vault            : %s/%s", config.VAULT_PATH, config.SAVE_FOLDER)


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
    app.add_handler(MessageHandler(filters.TEXT | filters.CAPTION, handle_message))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
