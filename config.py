import json
import os

APP_NAME = "VoiceType"
BUNDLE_ID = "com.voicetype.app"

CONFIG_DIR = os.path.expanduser("~/Library/Application Support/VoiceType")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
LAUNCHAGENT_DIR = os.path.expanduser("~/Library/LaunchAgents")
LAUNCHAGENT_FILE = os.path.join(LAUNCHAGENT_DIR, f"{BUNDLE_ID}.plist")

# Proxy to avoid country-based blocks
GROQ_PROXY_BASE_URL = "https://groq-22372.deno.dev"

DEFAULT_CONFIG = {
    "groq_api_key": "",
    "language": "ru",
    "model": "whisper-large-v3-turbo",
    "format_with_llm": True,
    "llm_model": "llama-3.3-70b-versatile",
    "max_recording_seconds": 120,
    "base_url": GROQ_PROXY_BASE_URL,
}


def _ensure_config_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load_config():
    """Load config from JSON file, merging with defaults."""
    if not os.path.exists(CONFIG_FILE):
        return DEFAULT_CONFIG.copy()
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        saved = json.load(f)
    config = DEFAULT_CONFIG.copy()
    config.update(saved)
    return config


def save_config(config):
    """Save config to JSON file."""
    _ensure_config_dir()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def init_config():
    """Create default config file if it doesn't exist. Returns config."""
    _ensure_config_dir()
    if not os.path.exists(CONFIG_FILE):
        save_config(DEFAULT_CONFIG)
    return load_config()


def install_autostart(app_path=None):
    """Install LaunchAgent for auto-start at login."""
    if app_path is None:
        app_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "app.py"))

    # Determine the executable
    # If running as .app bundle, use the bundle path
    # If running as script, use python3 + script path
    import sys
    python_path = sys.executable

    os.makedirs(LAUNCHAGENT_DIR, exist_ok=True)

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{BUNDLE_ID}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>{app_path}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""
    with open(LAUNCHAGENT_FILE, "w") as f:
        f.write(plist_content)


def uninstall_autostart():
    """Remove LaunchAgent for auto-start."""
    if os.path.exists(LAUNCHAGENT_FILE):
        os.unlink(LAUNCHAGENT_FILE)


def is_autostart_installed():
    """Check if auto-start LaunchAgent exists."""
    return os.path.exists(LAUNCHAGENT_FILE)
