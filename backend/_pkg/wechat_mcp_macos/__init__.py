"""WeChat MCP Server for macOS — read and send WeChat messages via AI."""

__version__ = "0.1.1"


def main():
    """CLI entry point: starts the MCP server (stdio transport)."""
    from . import liveness
    from .server import mcp

    # Arm the orphan guards before mcp.run() blocks on the stdio loop. A server
    # whose client is already gone (stdin is /dev/null, or the launching session
    # died) must exit instead of lingering until reboot.
    liveness.install(mcp)
    mcp.run()
