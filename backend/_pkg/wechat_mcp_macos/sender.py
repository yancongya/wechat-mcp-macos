"""
WeChat message sender via macOS osascript UI automation.

Since the WeChat database is read-only, sending messages requires
automating the WeChat desktop client via AppleScript/System Events.

Prerequisites:
  - WeChat desktop client must be logged in and running
  - The app running this code (Terminal, Claude.app, etc.) must have
    Accessibility permissions in System Settings → Privacy & Security
"""

import logging
import subprocess
import time

logger = logging.getLogger(__name__)


def _run_osascript(script: str) -> tuple[bool, str]:
    """Run an AppleScript and return (success, output)."""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "osascript timed out"
    except Exception as e:
        return False, str(e)


def activate_wechat() -> tuple[bool, str]:
    """Bring WeChat to the foreground."""
    return _run_osascript(
        """
        tell application "WeChat"
            activate
            reopen
        end tell
        """
    )


def get_window_info() -> dict | None:
    """Get WeChat window position and size.

    Returns:
        {"x": int, "y": int, "width": int, "height": int} or None.
    """
    ok, output = _run_osascript(
        """
        tell application "System Events"
            tell process "WeChat"
                set frontmost to true
                set winPos to position of window 1
                set winSize to size of window 1
                return (item 1 of winPos) & "," & (item 2 of winPos) & "," & (item 1 of winSize) & "," & (item 2 of winSize)
            end tell
        end tell
        """
    )
    if not ok:
        logger.error("Failed to get window info: %s", output)
        return None

    try:
        parts = [int(p.strip()) for p in output.split(",")]
        return {"x": parts[0], "y": parts[1], "width": parts[2], "height": parts[3]}
    except (ValueError, IndexError):
        logger.error("Failed to parse window info: %s", output)
        return None


def search_and_select_chat(chat_name: str) -> tuple[bool, str]:
    """Search for a contact/group in WeChat and select it.

    Uses the search box to find and click on the chat.
    """
    ok, msg = activate_wechat()
    if not ok:
        return False, f"Failed to activate WeChat: {msg}"

    time.sleep(0.3)
    win = get_window_info()
    if not win:
        return False, "Cannot get WeChat window info"

    # Search box position: ~17% from left, ~50px from top
    search_x = win["x"] + int(win["width"] * 0.17)
    search_y = win["y"] + 50

    # Click search box, paste chat name, wait for results
    script = f"""
        set the clipboard to "{chat_name}"
        tell application "System Events"
            tell process "WeChat"
                set frontmost to true
                delay 0.2
                click at {{{search_x}, {search_y}}}
                delay 0.3
                keystroke "v" using command down
                delay 0.8
                -- Press Return to select first result
                key code 36
                delay 0.3
            end tell
        end tell
    """
    return _run_osascript(script)


def send_message(text: str, chat_name: str | None = None) -> tuple[bool, str]:
    """Send a message in WeChat.

    If chat_name is provided, searches and selects that chat first.
    Otherwise sends to the currently active chat.

    Args:
        text: Message text to send.
        chat_name: Optional contact/group name to switch to.

    Returns:
        (success, message) tuple.
    """
    if chat_name:
        ok, msg = search_and_select_chat(chat_name)
        if not ok:
            return False, f"Failed to select chat '{chat_name}': {msg}"
        time.sleep(0.3)

    ok, msg = activate_wechat()
    if not ok:
        return False, f"Failed to activate WeChat: {msg}"

    win = get_window_info()
    if not win:
        return False, "Cannot get WeChat window info"

    # Input box position: ~55% from left, ~96% from top
    input_x = win["x"] + int(win["width"] * 0.55)
    input_y = win["y"] + int(win["height"] * 0.96)

    # Use osascript to set clipboard (handles CJK correctly)
    # Then click input box, paste, and send
    # Note: we use heredoc-style to avoid quote escaping issues
    escaped_text = text.replace("\\", "\\\\").replace('"', '\\"')
    script = f"""
        set the clipboard to "{escaped_text}"
        tell application "System Events"
            tell process "WeChat"
                set frontmost to true
                delay 0.3
                click at {{{input_x}, {input_y}}}
                delay 0.3
                keystroke "v" using command down
                delay 0.3
                key code 36
            end tell
        end tell
    """
    ok, msg = _run_osascript(script)
    if ok:
        return True, f"Message sent to {'current chat' if not chat_name else chat_name}"
    return False, f"Failed to send: {msg}"
