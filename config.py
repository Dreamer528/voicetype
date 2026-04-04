import json
import logging
import os
import subprocess

log = logging.getLogger("VoiceType")

APP_NAME = "VoiceType"
BUNDLE_ID = "com.voicetype.app"

CONFIG_DIR = os.path.expanduser("~/Library/Application Support/VoiceType")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
LAUNCHAGENT_DIR = os.path.expanduser("~/Library/LaunchAgents")
LAUNCHAGENT_FILE = os.path.join(LAUNCHAGENT_DIR, f"{BUNDLE_ID}.plist")

# Proxy to avoid country-based blocks
GROQ_PROXY_BASE_URL = "https://groq-22372.deno.dev"

# Keychain service/account for API key
KEYCHAIN_SERVICE = "VoiceType"
KEYCHAIN_ACCOUNT = "groq_api_key"

DEFAULT_CONFIG = {
    "groq_api_key": "",
    "language": "ru",
    "model": "whisper-large-v3-turbo",
    "format_with_llm": True,
    "llm_model": "llama-3.3-70b-versatile",
    "max_recording_seconds": 120,
    "base_url": GROQ_PROXY_BASE_URL,
    "transcription_mode": "cloud",          # cloud | local | auto
    "local_whisper_model": "base",          # tiny | base | small | medium | large-v3
    "openrouter_api_key": "",
    "qa_model": "nvidia/nemotron-3-super-120b-a12b:free",
}


# --- Keychain ---

def get_keychain_key():
    """Read API key from macOS Keychain. Returns None if not found."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
             "-a", KEYCHAIN_ACCOUNT, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def set_keychain_key(api_key):
    """Store API key in macOS Keychain. Returns True on success."""
    try:
        subprocess.run(
            ["security", "add-generic-password", "-U",
             "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT, "-w", api_key],
            capture_output=True, timeout=5,
        )
        return True
    except Exception:
        return False


def delete_keychain_key():
    """Remove API key from macOS Keychain."""
    try:
        subprocess.run(
            ["security", "delete-generic-password",
             "-s", KEYCHAIN_SERVICE, "-a", KEYCHAIN_ACCOUNT],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


# --- Config ---

def _ensure_config_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)


def load_config():
    """Load config from JSON file, merging with defaults.

    API key resolution order: Keychain > JSON file > empty.
    If JSON has a key but Keychain doesn't, migrates to Keychain.
    """
    if not os.path.exists(CONFIG_FILE):
        config = DEFAULT_CONFIG.copy()
    else:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            config = DEFAULT_CONFIG.copy()
            config.update(saved)
        except (json.JSONDecodeError, IOError) as e:
            log.warning("Не удалось загрузить конфиг: %s, используются настройки по умолчанию", e)
            config = DEFAULT_CONFIG.copy()

    # API key: prefer Keychain
    keychain_key = get_keychain_key()
    json_key = config.get("groq_api_key", "")

    if keychain_key:
        config["groq_api_key"] = keychain_key
        # Remove from JSON if still there
        if json_key:
            config_for_disk = config.copy()
            config_for_disk["groq_api_key"] = ""
            _save_config_to_disk(config_for_disk)
    elif json_key:
        # Migrate JSON key to Keychain
        if set_keychain_key(json_key):
            log.info("API ключ перенесён в Keychain")
            config_for_disk = config.copy()
            config_for_disk["groq_api_key"] = ""
            _save_config_to_disk(config_for_disk)

    return config


def _save_config_to_disk(config):
    """Write config dict to JSON file."""
    _ensure_config_dir()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def save_config(config):
    """Save config to JSON file. API key goes to Keychain if possible."""
    api_key = config.get("groq_api_key", "")
    config_for_disk = config.copy()

    if api_key:
        if set_keychain_key(api_key):
            config_for_disk["groq_api_key"] = ""  # Don't store in JSON
        # else: fallback to JSON (Keychain unavailable)

    _save_config_to_disk(config_for_disk)


def init_config():
    """Create default config file if it doesn't exist. Returns config."""
    _ensure_config_dir()
    if not os.path.exists(CONFIG_FILE):
        _save_config_to_disk(DEFAULT_CONFIG)
    return load_config()


def install_autostart(app_path=None):
    """Install LaunchAgent for auto-start at login."""
    if app_path is None:
        app_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "app.py"))

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
