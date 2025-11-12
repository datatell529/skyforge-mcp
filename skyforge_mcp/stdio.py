from __future__ import annotations

import logging

from .main import mcp  # reuse the FastMCP server instance and handlers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    """
    StdIO entry point for MCP clients (e.g., Cursor, Claude Desktop).
    """
    mcp.run()


if __name__ == "__main__":
    main()


