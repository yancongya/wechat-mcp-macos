"""
Key extraction — compile and run the C memory scanner to extract
WeChat database encryption keys from process memory.

Credits:
  - ylytdeng/wechat-decrypt: Original C memory scanner
  - Zhan (盏): Python fallback matching for macOS sandbox issues

Requires:
  - macOS with Xcode command-line tools (for `cc`)
  - WeChat must be ad-hoc signed (`sudo codesign --force --deep --sign -`)
  - sudo privileges for memory scanning
"""

import json
import logging
import os
import shlex
import subprocess
from pathlib import Path

from .config import DATA_DIR, KEYS_FILE, load_config

logger = logging.getLogger(__name__)

C_SOURCE = str(Path(__file__).parent / "c_src" / "find_keys_macos.c")
C_BINARY = str(DATA_DIR / "find_keys_macos")
EXTRACT_LOG = str(DATA_DIR / "extract_keys.log")
DEFAULT_WECHAT_APP = "/Applications/WeChat.app"
WECHAT_PROCESS_NAMES = ("WeChat", "WeChatAppEx", "微信")
WECHAT_PROCESS_PATTERNS = (
    r"/WeChat\.app/Contents/MacOS/WeChat($| )",
    r"/WeChatAppEx\.app/Contents/MacOS/WeChatAppEx($| )",
)


def _first_pid(args: list[str]) -> int | None:
    try:
        result = subprocess.run(args, capture_output=True, text=True)
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.isdigit():
                return int(line)
    except Exception:
        pass
    return None


def get_wechat_pid() -> int | None:
    """Find the WeChat main process PID."""
    for name in WECHAT_PROCESS_NAMES:
        pid = _first_pid(["pgrep", "-x", name])
        if pid:
            return pid
    for pattern in WECHAT_PROCESS_PATTERNS:
        pid = _first_pid(["pgrep", "-f", pattern])
        if pid:
            return pid
    return None


def is_wechat_running() -> bool:
    """Check if WeChat is currently running."""
    return get_wechat_pid() is not None


def get_wechat_app_path() -> str | None:
    """Get the WeChat.app path on this system."""
    try:
        result = subprocess.run(
            ["osascript", "-e", 'POSIX path of (path to application "WeChat")'],
            capture_output=True,
            text=True,
        )
        path = result.stdout.strip()
        if result.returncode == 0 and path and os.path.isdir(path):
            return path.rstrip("/")
    except Exception:
        pass
    if os.path.isdir(DEFAULT_WECHAT_APP):
        return DEFAULT_WECHAT_APP
    return None


def is_wechat_signed() -> bool:
    """Check if WeChat has been ad-hoc signed (hardened runtime removed)."""
    app_path = get_wechat_app_path()
    if not app_path:
        return False
    try:
        result = subprocess.run(
            ["codesign", "-dvv", app_path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        return "runtime" not in result.stderr.lower()
    except Exception:
        return False


def sign_wechat() -> tuple[bool, str]:
    """Ad-hoc sign WeChat to remove hardened runtime.

    Returns:
        (success, message) tuple.
    """
    app_path = get_wechat_app_path()
    if not app_path:
        return False, "WeChat.app not found"

    if is_wechat_running():
        return False, "Please quit WeChat before signing"

    cmd = f"codesign --force --deep --sign - {shlex.quote(app_path)}"
    try:
        result = subprocess.run(
            [
                "osascript",
                "-e",
                f"do shell script {json.dumps(cmd)} with administrator privileges",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, f"Successfully signed {app_path}"
        return False, f"Signing failed: {result.stderr}"
    except subprocess.TimeoutExpired:
        return False, "Signing timed out"
    except Exception as e:
        return False, f"Signing error: {e}"


def compile_scanner() -> bool:
    """Compile the C memory key scanner."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if os.path.exists(C_BINARY):
        if os.path.getmtime(C_BINARY) >= os.path.getmtime(C_SOURCE):
            return True

    try:
        result = subprocess.run(
            ["cc", "-O2", "-o", C_BINARY, C_SOURCE, "-framework", "Foundation"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error("Compilation failed: %s", result.stderr)
            return False
        return True
    except Exception as e:
        logger.error("Compilation failed: %s", e)
        return False


def extract_keys() -> dict | None:
    """Run the C scanner to extract keys. Requires sudo.

    Returns:
        dict mapping db_rel_path -> {"enc_key": hex_string}, or None on failure.
    """
    if not compile_scanner():
        return None

    pid = get_wechat_pid()
    if not pid:
        return None

    home_dir = os.path.expanduser("~")
    db_dir = load_config().get("db_dir", "")

    try:
        # Try passwordless sudo first
        result = subprocess.run(
            ["sudo", "-n", C_BINARY, str(pid), home_dir, db_dir],
            capture_output=True,
            text=True,
            cwd=str(DATA_DIR),
            timeout=60,
        )
        with open(EXTRACT_LOG, "w", encoding="utf-8") as f:
            f.write("=== sudo -n stdout ===\n")
            f.write(result.stdout)
            f.write("\n=== sudo -n stderr ===\n")
            f.write(result.stderr)

        if result.returncode != 0:
            # Fall back to osascript sudo prompt
            shell_command = (
                f"cd {shlex.quote(str(DATA_DIR))} && "
                f"{shlex.quote(C_BINARY)} {pid} "
                f"{shlex.quote(home_dir)} {shlex.quote(db_dir)}"
            )
            result = subprocess.run(
                [
                    "osascript",
                    "-e",
                    f"do shell script {json.dumps(shell_command)} with administrator privileges",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            with open(EXTRACT_LOG, "a", encoding="utf-8") as f:
                f.write("\n=== osascript stdout ===\n")
                f.write(result.stdout)
                f.write("\n=== osascript stderr ===\n")
                f.write(result.stderr)
            if result.returncode != 0:
                return None

    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None

    # Read the output keys file
    keys_path = DATA_DIR / "all_keys.json"
    if not keys_path.exists():
        return None

    try:
        with open(keys_path) as f:
            keys = json.load(f)
        keys = {k: v for k, v in keys.items() if not k.startswith("_")}
    except (json.JSONDecodeError, OSError):
        return None

    # If C scanner couldn't match DBs (root can't read sandbox), rematch in Python
    if not keys and db_dir and os.path.isdir(db_dir):
        keys = _rematch_keys_from_log(db_dir)

    return keys if keys else None


def _parse_raw_keys_from_log() -> list[tuple[str, str]]:
    """Parse key+salt pairs from the extraction log."""
    raw_keys: list[tuple[str, str]] = []
    if not os.path.exists(EXTRACT_LOG):
        return raw_keys
    try:
        with open(EXTRACT_LOG, encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) < 3:
                    continue
                key_hex = parts[-2]
                salt_hex = parts[-1]
                if len(key_hex) == 64 and len(salt_hex) == 32:
                    try:
                        bytes.fromhex(key_hex)
                        bytes.fromhex(salt_hex)
                        raw_keys.append((key_hex.lower(), salt_hex.lower()))
                    except ValueError:
                        continue
    except OSError:
        pass
    return raw_keys


def _rematch_keys_from_log(db_dir: str) -> dict:
    """Rematch keys from scanner log using Python (user permissions).

    Solves the issue where root can't read macOS sandbox files.
    """
    raw_keys = _parse_raw_keys_from_log()
    if not raw_keys:
        return {}

    logger.info("Rematching %d keys from log with Python...", len(raw_keys))

    salt_to_key = {salt: key for key, salt in raw_keys}
    matched: dict[str, dict] = {}

    for root, _dirs, files in os.walk(db_dir):
        for fname in files:
            if not fname.endswith(".db"):
                continue
            full_path = os.path.join(root, fname)
            rel = os.path.relpath(full_path, db_dir).replace("\\", "/")
            try:
                with open(full_path, "rb") as f:
                    header = f.read(16)
                if len(header) < 16 or header[:15] == b"SQLite format 3":
                    continue
                file_salt = header.hex().lower()
                if file_salt in salt_to_key:
                    matched[rel] = {"enc_key": salt_to_key[file_salt]}
            except OSError:
                continue

    if matched:
        try:
            with open(str(KEYS_FILE), "w") as f:
                json.dump(matched, f, indent=2)
            logger.info("Python rematching succeeded: %d databases", len(matched))
        except OSError:
            pass

    return matched


def get_cached_keys() -> dict | None:
    """Get cached keys without re-extracting."""
    cfg = load_config()
    path = Path(cfg.get("keys_file") or KEYS_FILE)
    if not path.is_file():
        path = KEYS_FILE
    if not path.exists():
        return None
    try:
        with open(path) as f:
            keys = json.load(f)
        if not isinstance(keys, dict):
            return None
        keys = {k: v for k, v in keys.items() if not k.startswith("_")}
        return keys if keys else None
    except (json.JSONDecodeError, OSError):
        return None


def check_new_databases(db_dir: str, keys: dict) -> list[str]:
    """Check for new encrypted databases that are missing keys."""
    missing = []
    normalized_keys = {k.replace("\\", "/") for k in keys}
    for root, _dirs, files in os.walk(db_dir):
        for fname in files:
            if not fname.endswith(".db"):
                continue
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, db_dir).replace("\\", "/")
            if rel in normalized_keys:
                continue
            try:
                with open(full, "rb") as f:
                    header = f.read(16)
                if len(header) < 16 or header[:15] == b"SQLite format 3":
                    continue
                missing.append(rel)
            except OSError:
                continue
    return sorted(missing)
