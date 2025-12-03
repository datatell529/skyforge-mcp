from __future__ import annotations

"""
Module entry point so `python -m skyforge_mcp` works.
Defaults to stdio mode (best for MCP clients like Claude Desktop / Cursor).
"""

from .stdio import main as stdio_main


def main() -> None:
    stdio_main()


if __name__ == "__main__":
    main()

"""Module runner to support `python -m skyforge_mcp`."""

from .main import main


if __name__ == "__main__":
    main()


