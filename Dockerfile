FROM python:3.12-slim

# Install uv binary from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Environment settings for containerized Python
ENV UV_COMPILE_BYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FASTMCP_HOST=0.0.0.0
ENV FASTMCP_PORT=8000

# Install dependencies first for optimal Docker layer caching
COPY pyproject.toml README.md LICENSE ./
RUN uv pip install --system --no-cache "."

# Copy source tree and install project cleanly (non-editable in production image)
COPY src/ ./src/
RUN uv pip install --system --no-cache "."

# Create unprivileged application user
RUN groupadd -g 10001 appuser \
    && useradd -u 10001 -g appuser -m -d /home/appuser appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["moomoo-api-mcp"]
