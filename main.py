"""Compatibility wrapper that delegates to the packaged entrypoint.

Kept for local dev. Prefer `python -m skyforge_mcp` or `skyforge-mcp`.
"""

import logging
import sys
from skyforge_mcp.main import app, main  # re-export for uvicorn/CLI

# Configure logging for credential validation
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

__all__ = ["app", "main"]

def validate_credentials():
    """Validate SkySpark credentials before starting server."""
    try:
        from app.skyspark.client import SkySpark
        # Test client initialization - will raise exception if credentials invalid
        SkySpark()
        logger.info("✓ SkySpark credentials validated successfully")
        return True
    except Exception as e:
        logger.error(f"✗ FAILED TO VALIDATE SKYSPARK CREDENTIALS: {e}")
        logger.error("Check environment variables: SKYSPARK_URI, SKYSPARK_USERNAME, SKYSPARK_PASSWORD")
        return False

if __name__ == "__main__":
    # Validate credentials before starting server
    if not validate_credentials():
        sys.exit(1)
    # Allow `uv run main.py` to work for local/dev
    main()