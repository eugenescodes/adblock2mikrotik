# ─── Stage 1: builder — resolve dependencies into a self-contained venv ───
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /bin/

ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_FROZEN=1 \
    UV_NO_DEV=1 \
    UV_NO_CACHE=1

WORKDIR /app

# Only the lock files are needed to build the venv (package = false in pyproject)
COPY pyproject.toml uv.lock ./
RUN uv sync

# ─── Stage 2: runtime — no uv, only the venv and the script ───
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

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
# config.toml.example is the built-in fallback for sources — must sit next to the script
COPY convert_to_hosts.py config.toml.example ./

USER appuser

VOLUME /output

ENTRYPOINT ["python", "convert_to_hosts.py"]
