"""
FastMCP server for WeChat on macOS.

Provides 8 tools for reading and sending WeChat messages:
  - get_status: Health check
  - setup: Guided first-time setup
  - list_chats: Recent conversations
  - read_messages: Read messages from a chat
  - check_new: Lightweight new-message check for polling
  - search: Cross-chat keyword search
  - list_groups: All group chats
  - send: Send a message via UI automation
"""

import logging
import time

from fastmcp import FastMCP

from .config import DATA_DIR, KEYS_FILE, load_config, save_config
from .db import WeChatDB
from .key_extractor import (
    check_new_databases,
    compile_scanner,
    extract_keys,
    get_cached_keys,
    get_wechat_pid,
    is_wechat_running,
    is_wechat_signed,
    sign_wechat,
)
from .sender import send_message as _send_message

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "WeChat MCP (macOS)",
    instructions=(
        "Read and send WeChat messages on macOS. "
        "Decrypts the local WeChat SQLite database for reading, "
        "and uses osascript UI automation for sending."
    ),
)

# Lazy-initialized database instance
_db: WeChatDB | None = None


def _get_db() -> WeChatDB | None:
    """Get or create the WeChatDB instance."""
    global _db
    if _db is not None:
        return _db

    cfg = load_config()
    if not cfg["db_dir"]:
        return None

    keys = get_cached_keys()
    if not keys:
        return None

    _db = WeChatDB(cfg["db_dir"], keys)
    return _db


def _refresh_db() -> WeChatDB | None:
    """Force re-create the WeChatDB instance (after key extraction, etc.)."""
    global _db
    _db = None
    return _get_db()


# ── Tool: get_status ──


@mcp.tool()
def get_status() -> str:
    """Check WeChat MCP server status: config, keys, WeChat process, etc."""
    cfg = load_config()
    lines = ["=== WeChat MCP Status ==="]

    # Config
    if cfg["db_dir"]:
        lines.append(f"DB directory: {cfg['db_dir']}")
    else:
        lines.append("DB directory: NOT CONFIGURED (run setup first)")

    # Keys
    keys = get_cached_keys()
    if keys:
        lines.append(f"Encryption keys: {len(keys)} databases")
    else:
        lines.append("Encryption keys: NOT EXTRACTED (run setup)")

    # WeChat process
    pid = get_wechat_pid()
    if pid:
        lines.append(f"WeChat process: running (PID {pid})")
    else:
        lines.append("WeChat process: NOT RUNNING")

    # Signing
    if is_wechat_signed():
        lines.append("WeChat signing: ad-hoc signed (ready)")
    else:
        lines.append("WeChat signing: hardened runtime (needs re-signing)")

    # DB instance
    db = _get_db()
    if db:
        lines.append("Database: ready")
    else:
        lines.append("Database: not initialized")

    # New databases check
    if keys and cfg["db_dir"]:
        missing = check_new_databases(cfg["db_dir"], keys)
        if missing:
            lines.append(f"Warning: {len(missing)} new databases missing keys")

    return "\n".join(lines)


# ── Tool: setup ──


@mcp.tool()
def setup(step: str = "auto") -> str:
    """Guided setup for first-time users.

    Steps:
      - "auto": Run all steps automatically
      - "detect": Detect WeChat database directory
      - "sign": Ad-hoc sign WeChat (removes hardened runtime, requires sudo)
      - "extract": Extract encryption keys from WeChat memory (requires sudo)

    The typical first-time flow is:
      1. Quit WeChat
      2. Run setup("sign") to re-sign WeChat
      3. Restart WeChat and log in
      4. Run setup("extract") to get encryption keys
    Or just run setup("auto") and follow the instructions.
    """
    if step == "detect":
        return _setup_detect()
    elif step == "sign":
        return _setup_sign()
    elif step == "extract":
        return _setup_extract()
    elif step == "auto":
        return _setup_auto()
    else:
        return f"Unknown step: {step}. Use 'auto', 'detect', 'sign', or 'extract'."


def _setup_detect() -> str:
    cfg = load_config()
    if cfg["db_dir"]:
        return f"WeChat database directory detected: {cfg['db_dir']}"
    return (
        "Could not auto-detect WeChat database directory. "
        "Make sure WeChat is installed and has been logged in at least once."
    )


def _setup_sign() -> str:
    if is_wechat_signed():
        return "WeChat is already ad-hoc signed. No action needed."
    if is_wechat_running():
        return (
            "WeChat is running. Please quit WeChat first, then run setup('sign') again.\n"
            "You can quit WeChat from the menu bar or run: osascript -e 'quit app \"WeChat\"'"
        )
    ok, msg = sign_wechat()
    if ok:
        return (
            f"{msg}\n\n"
            "Now restart WeChat and log in, then run setup('extract') to get encryption keys.\n"
            "Note: You may need to re-authorize WeChat's screen recording permission in "
            "System Settings → Privacy & Security → Screen Recording."
        )
    return f"Failed to sign WeChat: {msg}"


def _setup_extract() -> str:
    if not is_wechat_running():
        return "WeChat is not running. Please start WeChat and log in first."
    if not is_wechat_signed():
        return (
            "WeChat still has hardened runtime. Run setup('sign') first "
            "(quit WeChat before signing)."
        )
    if not compile_scanner():
        return (
            "Failed to compile the key scanner. "
            "Make sure Xcode command-line tools are installed: xcode-select --install"
        )

    keys = extract_keys()
    if keys:
        _refresh_db()
        return f"Successfully extracted keys for {len(keys)} databases! You're all set."
    return (
        "Key extraction failed. Check that:\n"
        "1. WeChat is running and logged in\n"
        "2. WeChat has been ad-hoc signed\n"
        "3. You approved the sudo password prompt\n"
        f"See log at: {DATA_DIR / 'extract_keys.log'}"
    )


def _setup_auto() -> str:
    lines = ["=== WeChat MCP Auto Setup ===\n"]

    # Step 1: Detect DB
    cfg = load_config()
    if cfg["db_dir"]:
        lines.append(f"[1/3] DB directory: {cfg['db_dir']}")
    else:
        lines.append("[1/3] DB directory: NOT FOUND")
        lines.append("  Make sure WeChat has been installed and logged in once.")
        return "\n".join(lines)

    # Step 2: Check signing
    if is_wechat_signed():
        lines.append("[2/3] WeChat signing: OK (ad-hoc signed)")
    else:
        lines.append("[2/3] WeChat signing: NEEDS RE-SIGNING")
        if is_wechat_running():
            lines.append("  → Please quit WeChat, then run: setup('sign')")
        else:
            lines.append("  → Run: setup('sign')")
        return "\n".join(lines)

    # Step 3: Check keys
    keys = get_cached_keys()
    if keys:
        lines.append(f"[3/3] Encryption keys: {len(keys)} databases")
        lines.append("\nAll set! You can now use list_chats, read_messages, etc.")
    else:
        if is_wechat_running():
            lines.append("[3/3] Encryption keys: NOT EXTRACTED")
            lines.append("  → Run: setup('extract')")
        else:
            lines.append("[3/3] WeChat not running — start it and log in, then run: setup('extract')")

    return "\n".join(lines)


# ── Tool: list_chats ──


@mcp.tool()
def list_chats(limit: int = 20) -> str:
    """List recent WeChat conversations (groups and DMs).

    Args:
        limit: Maximum number of conversations to return (default 20).
    """
    db = _get_db()
    if not db:
        return "Database not ready. Run get_status() to check, then setup() if needed."

    sessions = db.get_recent_sessions(limit=limit)
    if not sessions:
        return "No recent conversations found."

    lines = []
    for s in sessions:
        tag = "Group" if s["is_group"] else "DM"
        unread = f" ({s['unread']} unread)" if s["unread"] else ""
        summary = s["summary"][:60] if s["summary"] else ""
        lines.append(f"[{tag}] {s['name']}{unread} — {s['time_str']}")
        if summary:
            lines.append(f"  └ {summary}")
        lines.append(f"  id: {s['username']}")
    return "\n".join(lines)


# ── Tool: read_messages ──


@mcp.tool()
def read_messages(
    chat: str,
    limit: int = 50,
    since_hours: float = 0,
) -> str:
    """Read messages from a WeChat chat.

    Args:
        chat: Chat username/ID (e.g. "xxx@chatroom") or display name.
              Use list_chats() or list_groups() to find the right ID.
        limit: Maximum number of messages (default 50).
        since_hours: Only get messages from the last N hours (0 = use limit).

    Note: sender is empty for messages sent by the account owner.
    """
    db = _get_db()
    if not db:
        return "Database not ready. Run get_status() to check, then setup() if needed."

    # Resolve name to username if needed
    username = db.resolve_username(chat)
    if not username:
        return f"Chat '{chat}' not found. Use list_chats() to see available chats."

    since_ts = 0
    if since_hours > 0:
        since_ts = int(time.time() - since_hours * 3600)

    messages = db.get_messages(username, since_ts=since_ts, limit=limit)
    if not messages:
        return "No messages found."

    cfg = load_config()
    self_name = cfg.get("self_name", "") or "Me"

    lines = []
    for m in messages:
        sender = m["sender"] if m["sender"] else self_name
        lines.append(f"[{m['time_str']}] {sender}: {m['text']}")
    return "\n".join(lines)


# ── Tool: check_new ──


@mcp.tool()
def check_new(
    chats: str,
    since_minutes: float = 5,
) -> str:
    """Lightweight check for new messages — ideal for polling loops.

    Checks one or more chats for messages received within the last N minutes.
    Returns only counts (no message content), making it fast and token-efficient.
    Use read_messages() to fetch actual content when new messages are detected.

    Typical polling workflow:
      1. Call check_new("groupA, groupB", since_minutes=3) every few minutes
      2. If count > 0 for a chat, call read_messages(chat, since_hours=0.05)
      3. Process and optionally reply with send()

    Args:
        chats: Comma-separated chat names or IDs to monitor.
        since_minutes: Check for messages in the last N minutes (default 5).
    """
    db = _get_db()
    if not db:
        return "Database not ready. Run get_status() to check, then setup() if needed."

    since_ts = int(time.time() - since_minutes * 60)
    chat_list = [c.strip() for c in chats.split(",") if c.strip()]

    if not chat_list:
        return "Please specify at least one chat to check."

    lines = []
    total_new = 0
    for chat_name in chat_list:
        username = db.resolve_username(chat_name)
        if not username:
            lines.append(f"  {chat_name}: not found")
            continue
        count = db.count_messages_since(username, since_ts)
        if count > 0:
            lines.append(f"  {chat_name}: {count} new")
            total_new += count
        else:
            lines.append(f"  {chat_name}: no new messages")

    header = f"Checked {len(chat_list)} chat(s), last {since_minutes} min:"
    if total_new > 0:
        header += f" [{total_new} new message(s) total]"
    lines.insert(0, header)
    return "\n".join(lines)


# ── Tool: search_messages ──


@mcp.tool()
def search(
    keywords: str,
    chat_names: str = "",
    hours_back: float = 24,
) -> str:
    """Search WeChat messages by keywords.

    Args:
        keywords: Space-separated search terms (AND logic).
        chat_names: Comma-separated chat names to search in.
                    Leave empty to search all group chats.
        hours_back: How far back to search, in hours (default 24).
    """
    db = _get_db()
    if not db:
        return "Database not ready. Run get_status() to check, then setup() if needed."

    kw_list = keywords.strip().split()
    if not kw_list:
        return "Please provide at least one keyword."

    end_ts = time.time()
    start_ts = end_ts - hours_back * 3600

    if chat_names.strip():
        names = [n.strip() for n in chat_names.split(",")]
        usernames = []
        for name in names:
            uname = db.resolve_username(name)
            if uname:
                usernames.append(uname)
        if not usernames:
            return f"No matching chats found for: {chat_names}"
    else:
        groups = db.get_groups()
        usernames = [g["username"] for g in groups]

    results = db.search_messages(kw_list, usernames, start_ts, end_ts)
    if not results:
        return f"No messages matching '{keywords}' found in the last {hours_back}h."

    lines = []
    total = 0
    for uname, msgs in results.items():
        group_name = msgs[0]["group_name"] if msgs else uname
        lines.append(f"\n── {group_name} ({len(msgs)} matches) ──")
        for m in msgs[:20]:  # Cap display per group
            lines.append(f"  [{m['time_str']}] {m['sender']}: {m['text']}")
        if len(msgs) > 20:
            lines.append(f"  ... and {len(msgs) - 20} more")
        total += len(msgs)

    lines.insert(0, f"Found {total} messages in {len(results)} chats:")
    return "\n".join(lines)


# ── Tool: list_groups ──


@mcp.tool()
def list_groups() -> str:
    """List all WeChat group chats."""
    db = _get_db()
    if not db:
        return "Database not ready. Run get_status() to check, then setup() if needed."

    groups = db.get_groups()
    if not groups:
        return "No group chats found."

    lines = [f"Found {len(groups)} groups:\n"]
    for g in groups:
        lines.append(f"  {g['name']}")
        lines.append(f"    id: {g['username']}")
    return "\n".join(lines)


# ── Tool: send_message ──


@mcp.tool()
def send(text: str, chat: str = "") -> str:
    """Send a message in WeChat via UI automation.

    Requires WeChat desktop client to be running and Accessibility
    permissions granted to the app running this server.

    Args:
        text: Message text to send.
        chat: Contact/group name to send to. Leave empty to send to
              the currently active chat window.
    """
    if not text.strip():
        return "Message text cannot be empty."

    if not is_wechat_running():
        return "WeChat is not running. Please start it first."

    ok, msg = _send_message(text, chat_name=chat if chat else None)
    if ok:
        return msg
    return f"Failed to send message: {msg}"
