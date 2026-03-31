"""Dictation history — stores recent transcriptions for quick access."""

import json
import logging
import os
from datetime import datetime

log = logging.getLogger("VoiceType")

HISTORY_DIR = os.path.expanduser("~/Library/Application Support/VoiceType")
HISTORY_FILE = os.path.join(HISTORY_DIR, "history.json")
MAX_ENTRIES = 30


def _ensure_dir():
    os.makedirs(HISTORY_DIR, exist_ok=True)


def load_history():
    """Load history from disk. Returns list of {text, timestamp} dicts."""
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(entries):
    """Save history list to disk."""
    _ensure_dir()
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log.error("Failed to save history: %s", e)


def add_entry(text):
    """Add a new dictation entry to history."""
    entries = load_history()
    entry = {
        "text": text,
        "timestamp": datetime.now().strftime("%d.%m %H:%M"),
    }
    entries.insert(0, entry)
    # Trim to max
    entries = entries[:MAX_ENTRIES]
    save_history(entries)
    return entries


def clear_history():
    """Clear all history."""
    save_history([])


def truncate_text(text, max_len=45):
    """Truncate text for menu display."""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + "..."
