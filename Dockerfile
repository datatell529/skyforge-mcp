# Skyforge MCP Server - Production Docker Image
# Alpine-based for minimal size
FROM ghcr.io/astral-sh/uv:python3.12-alpine

WORKDIR ./

# Install dependencies first (better layer caching)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY ./ ./

# HTTP/SSE endpoint
EXPOSE 8000

# Default: run HTTP server
# Override with: docker run ... uv run main.py (alternate entry)
CMD ["uv", "run", "python", "main.py"]
