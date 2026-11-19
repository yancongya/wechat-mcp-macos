"""
Configuration management for wechat-mcp-macos.

Stores config in ~/.wechat-mcp/config.json.
Auto-detects WeChat database directory on macOS.
Migrates from ~/.wechat-summary/ if that directory exists (for users of the
wechat-summary-share tool by ylytdeng).
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

DATA_DIR = Path.home() / ".wechat-mcp"
CONFIG_FILE = DATA_DIR / "config.json"
KEYS_FILE = DATA_DIR / "all_keys.json"
DECRYPTED_DIR = DATA_DIR / "decrypted"

# Legacy directory from wechat-summary-share tool
LEGACY_DATA_DIR = Path.home() / ".wechat-summary"

DEFAULT_CONFIG = {
    "db_dir": "",
    "keys_file": str(KEYS_FILE),
    "decrypted_dir": str(DECRYPTED_DIR),
    "self_name": "",  # display name for own messages (empty sender in DB)
}


def _read_json(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _sanitize(saved: dict | None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if not isinstance(saved, dict):
        return cfg
    for key in DEFAULT_CONFIG:
        val = saved.get(key)
        if isinstance(val, str):
            cfg[key] = val
    return cfg


def auto_detect_db_dir() -> str | None:
    """Auto-detect the WeChat SQLite database directory on macOS."""
    bases = [
        Path.home()
        / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files",
        Path.home()
        / "Library/Containers/com.tencent.xinWeChat/Data/Documents",
        Path.home()
        / "Library/Containers/com.tencent.xinWeChat/Data/Library/Application Support",
    ]

    candidates: list[tuple[int, float, str]] = []
    seen: set[str] = set()

    for base in bases:
        if not base.is_dir():
            continue
        for storage in base.rglob("db_storage"):
            if not storage.is_dir():
                continue
            s = str(storage)
            if s in seen:
                continue
            seen.add(s)

            score = 0
            if "/xwechat_files/" in s:
                score += 2
            if (storage / "contact" / "contact.db").is_file():
                score += 2
            if (storage / "session" / "session.db").is_file():
                score += 2
            try:
                mtime = storage.stat().st_mtime
            except OSError:
                mtime = 0.0
            candidates.append((score, mtime, s))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def _migrate_legacy() -> dict | None:
    """Migrate config from ~/.wechat-summary/ if it exists."""
    legacy_config = LEGACY_DATA_DIR / "config.json"
    legacy_keys = LEGACY_DATA_DIR / "all_keys.json"

    if not legacy_config.is_file():
        return None

    logger.info("Found legacy config at %s, migrating...", legacy_config)
    saved = _read_json(legacy_config)
    if saved is None:
        return None

    cfg = _sanitize(saved)

    # Copy keys file if it exists and ours doesn't
    if legacy_keys.is_file() and not KEYS_FILE.is_file():
        import shutil

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_keys, KEYS_FILE)
        logger.info("Copied keys file from %s", legacy_keys)
        cfg["keys_file"] = str(KEYS_FILE)

    cfg["decrypted_dir"] = str(DECRYPTED_DIR)
    return cfg


def load_config() -> dict:
    """Load configuration, auto-detecting on first run."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Try loading existing config
    saved = _read_json(CONFIG_FILE)
    if saved is not None:
        cfg = _sanitize(saved)
    else:
        # Try migrating from legacy
        cfg = _migrate_legacy()
        if cfg is None:
            cfg = dict(DEFAULT_CONFIG)

    # Auto-detect db_dir if missing
    if not cfg["db_dir"] or not os.path.isdir(cfg["db_dir"]):
        detected = auto_detect_db_dir()
        if detected:
            cfg["db_dir"] = detected
            logger.info("Auto-detected WeChat DB directory: %s", detected)

    save_config(cfg)
    return cfg


def save_config(cfg: dict) -> None:
    """Save configuration to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    normalized = _sanitize(cfg)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)
