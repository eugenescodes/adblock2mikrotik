# ─── Stage 1: builder ───
# Build the application in a separate image so the runtime image needs no uv.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /uvx /bin/

# Use the Python provided by this image instead of downloading a managed one.
ENV UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1

WORKDIR /app

# Install locked dependencies first so source changes do not invalidate this layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-install-project

# Include the package resources, then install a copy of the app into the venv.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

# ─── Stage 2: runtime ───
# Use the same Python version and Debian release as the builder: the venv
# refers to the interpreter at the same system path in both images.
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# Keep logs visible and avoid writing bytecode into the container filesystem.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    OUTPUT_DIR=/output

# Run without root privileges and provide a default output directory.
RUN groupadd --system --gid 999 appuser \
    && useradd --system --gid 999 --uid 999 --no-create-home appuser \
    && mkdir /output \
    && chown appuser:appuser /output

# Only the installed environment is needed at runtime; the app source and uv
# remain in the builder image.
COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv

WORKDIR /app
USER appuser

# The generated hosts file is written here by default.
VOLUME /output

# Pass Docker command-line arguments directly to the application.
ENTRYPOINT ["adblock2mikrotik"]
