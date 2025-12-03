# syntax=docker/dockerfile:1.7-labs
# Skyforge MCP Server - Production Docker Image
# Debian Bookworm-based image (glibc). Use -alpine for smaller musl-based image.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm

WORKDIR /app

# Avoid hardlink warnings when using BuildKit cache mounts
ENV UV_LINK_MODE=copy

# Install dependencies first (better layer caching)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# Copy application code
COPY ./ ./

# HTTP/SSE endpoint
EXPOSE 8000

# Default: run HTTP server
# Override with: docker run ... uv run main.py (alternate entry)
CMD ["uv", "run", "python", "main.py"]
