"""Liveness guards for the stdio MCP server.

Orphaned servers used to pile up on the mini. A session that starts this process
over Tailscale SSH (``tailscaled be-child ssh-monitor`` -> ``login -f``) hands it
``stdin = /dev/null``. The MCP stdio loop then never receives a request and never
receives EOF either, so the process outlives every reconnect and lives until
reboot (seven were found alive on 2026-11-19; the oldest had been up 2.5 days,
each holding a dead Tailscale session open).

Guards, all best-effort and armed only after a grace period:

1. ``stdin`` was ``/dev/null`` at launch -- no client can ever reach us -- exit.
2. our parent died -- either we were reparented to launchd during the run, or we
   were already ``ppid == 1`` when we started (the launching session died before
   the server got its bearings) -- the controlling client is gone -- exit. An
   stdio server whose client is gone cannot be revived by anyone, so this is
   never a live session; set ``WECHAT_MCP_PARENT_GUARD=0`` if that ever changes
   (for example if this is deployed under a LaunchAgent).
3. no MCP message for ``WECHAT_MCP_IDLE_TIMEOUT`` seconds -- exit.

Guard 1 has to sample fd 0 *before* ``mcp.run()``: the FastMCP 4 stdio transport
parks ``/dev/null`` on fd 0 once it starts (it keeps the real client stream on a
duplicated descriptor), so a late check cannot tell a live client from a dead
one. Sampling at install time sits before that redirect.

Every guard is a no-op if anything looks unexpected; a failure inside the
watchdog can never take the server down.

Tuning (environment):
  WECHAT_MCP_GUARDS=0            disable all guards
  WECHAT_MCP_GUARD_GRACE=8       seconds before the guards start enforcing
  WECHAT_MCP_GUARD_INTERVAL=15   seconds between inspections
  WECHAT_MCP_IDLE_TIMEOUT=21600  idle seconds before exit (0 = never)
  WECHAT_MCP_PARENT_GUARD=0      disable the parent-death guard only
"""

from __future__ import annotations

import logging
import os
import stat
import threading
import time

logger = logging.getLogger(__name__)

DEFAULT_GRACE = 8.0
DEFAULT_INTERVAL = 15.0
DEFAULT_IDLE_TIMEOUT = 6 * 3600.0

_last_activity = time.monotonic()
_installed = False


def note_activity() -> None:
    """Record that the client is still talking to us."""
    global _last_activity
    _last_activity = time.monotonic()


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.debug("liveness: ignoring non-numeric %s=%r", name, raw)
        return default


def _flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def stdin_is_devnull() -> bool:
    """True when fd 0 is the null device, i.e. no MCP client is attached.

    Only meaningful before the transport starts; see the module docstring.
    """
    try:
        fd = os.fstat(0)
        null = os.stat(os.devnull)
    except OSError:
        return False
    return stat.S_ISCHR(fd.st_mode) and fd.st_rdev == null.st_rdev


def _exit(reason: str) -> None:
    logger.warning("liveness: %s -- exiting", reason)
    # os._exit: the MCP loop may be parked in a blocking read that no amount of
    # unwinding will escape.
    os._exit(0)


def _watchdog(
    initial_ppid: int,
    client_attached: bool,
    idle_timeout: float,
    grace: float,
    interval: float,
    parent_guard: bool,
) -> None:
    started = time.monotonic()
    while True:
        time.sleep(interval)
        try:
            if time.monotonic() - started < grace:
                continue
            if not client_attached:
                _exit("stdin was %s at launch, so no MCP client can be attached" % os.devnull)
            if parent_guard:
                if initial_ppid <= 1:
                    _exit("orphaned at launch (ppid == %d), no controlling session" % initial_ppid)
                if os.getppid() != initial_ppid:
                    _exit("parent %d is gone (reparented to %d)" % (initial_ppid, os.getppid()))
            if idle_timeout > 0:
                idle = time.monotonic() - _last_activity
                if idle > idle_timeout + grace:
                    _exit("idle for %.0fs (limit %.0fs)" % (idle, idle_timeout))
        except Exception:
            logger.debug("liveness: watchdog iteration failed", exc_info=True)


def _activity_middleware():
    """FastMCP middleware that refreshes the idle clock on every MCP message."""
    try:
        from fastmcp.server.middleware import Middleware
    except Exception:  # pragma: no cover - fastmcp always present in practice
        return None

    class ActivityMiddleware(Middleware):
        async def on_message(self, context, call_next):
            note_activity()
            return await call_next(context)

    return ActivityMiddleware()


def install(mcp=None) -> bool:
    """Arm the guards. Returns True when they are running."""
    global _installed
    if _installed:
        return True
    if not _flag("WECHAT_MCP_GUARDS"):
        logger.info("liveness: guards disabled via WECHAT_MCP_GUARDS")
        return False

    # Sample fd 0 here, before mcp.run() lets the transport park /dev/null on it.
    client_attached = not stdin_is_devnull()
    grace = max(0.0, _env_float("WECHAT_MCP_GUARD_GRACE", DEFAULT_GRACE))
    interval = _env_float("WECHAT_MCP_GUARD_INTERVAL", DEFAULT_INTERVAL)
    if interval <= 0:
        interval = DEFAULT_INTERVAL
    idle_timeout = _env_float("WECHAT_MCP_IDLE_TIMEOUT", DEFAULT_IDLE_TIMEOUT)
    parent_guard = _flag("WECHAT_MCP_PARENT_GUARD")
    ppid = os.getppid()

    if mcp is not None:
        middleware = _activity_middleware()
        if middleware is not None:
            try:
                mcp.add_middleware(middleware)
            except Exception:
                logger.debug("liveness: add_middleware failed", exc_info=True)

    thread = threading.Thread(
        target=_watchdog,
        args=(ppid, client_attached, idle_timeout, grace, interval, parent_guard),
        name="wechat-mcp-liveness",
        daemon=True,
    )
    thread.start()
    _installed = True
    logger.info(
        "liveness: armed (client_attached=%s, ppid=%d, grace=%.0fs, interval=%.0fs, "
        "idle_timeout=%.0fs, parent_guard=%s)",
        client_attached, ppid, grace, interval, idle_timeout, parent_guard,
    )
    return True
