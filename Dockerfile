# ─── Stage 1: builder — install the package into a self-contained venv ───
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /bin/

ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_FROZEN=1 \
    UV_NO_DEV=1 \
    UV_NO_CACHE=1

WORKDIR /app

# Dependencies first, in their own layer: rebuilt only when the lock changes
COPY pyproject.toml uv.lock ./
RUN uv sync --no-install-project

# Then the package itself — non-editable, so the venv holds a real copy of the
# code and config.toml.example and doesn't need /app/src at runtime
COPY README.md ./
COPY src ./src
RUN uv sync --no-editable

# ─── Stage 2: runtime — no uv, no sources, only the venv ───
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    OUTPUT_DIR=/output

# Create non-root user and a dedicated output dir it owns — avoids permission
# conflicts with volume mounts
RUN useradd --system --no-create-home appuser \
    && mkdir /output \
    && chown appuser:appuser /output

COPY --from=builder /app/.venv /app/.venv

# Working directory is where an optional config.toml is looked up
# (mount it at /app/config.toml — see README)
WORKDIR /app

USER appuser

VOLUME /output

ENTRYPOINT ["adblock2mikrotik"]
