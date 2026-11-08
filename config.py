from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]

_raw_id = os.getenv("TELEGRAM_ALLOWED_USER_ID", "")
ALLOWED_USER_ID: Optional[int] = int(_raw_id) if _raw_id.strip() else None

VAULT_PATH: Path = Path(os.environ["OBSIDIAN_VAULT_PATH"]).expanduser().resolve()
SAVE_FOLDER: str = os.getenv("OBSIDIAN_SAVE_FOLDER", "Saved")

_raw_tags = os.getenv("DEFAULT_TAGS", "saved")
DEFAULT_TAGS: List[str] = [t.strip() for t in _raw_tags.split(",") if t.strip()]

# AI summary/analysis
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# Marker PDF converter (falls back to pymupdf4llm if unavailable)
_marker = os.getenv("MARKER_PATH", "")
MARKER_PATH: Optional[Path] = Path(_marker) if _marker and Path(_marker).is_file() else None
