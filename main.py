"""Compatibility wrapper that delegates to the packaged entrypoint.

Kept for local dev. Prefer `python -m skyforge_mcp` or `skyforge-mcp`.
"""

from skyforge_mcp.main import app, main  # re-export for uvicorn/CLI

__all__ = ["app", "main"]

if __name__ == "__main__":
    # Allow `uv run main.py` to work for local/dev
    main()