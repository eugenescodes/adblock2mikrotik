FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.10.9 /uv /uvx /bin/

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/usr/local \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_FROZEN=1 \
    UV_NO_DEV=1 \
    UV_NO_EDITABLE=1 \
    UV_NO_CACHE=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user early (before COPY, no extra layer with chown /app)
RUN useradd --system --no-create-home appuser

# Set the working directory
WORKDIR /app

# Dependency layers first for better cache reuse
COPY pyproject.toml uv.lock ./

# Sync the project into a new environment, asserting the lockfile is up to date
RUN uv sync

# Copy the project files
COPY . .

# Dedicated output dir owned by appuser — avoids permission conflict with volume mounts
RUN mkdir /output && chown appuser:appuser /output

# Declare the output directory as an environment variable for use in the script
ENV OUTPUT_DIR=/output

# Switch to the non-root user for better security
USER appuser

# Declare the output directory as a volume to allow users to mount it at runtime
VOLUME /output

# Set up entrypoint
ENTRYPOINT ["uv", "run", "convert_to_hosts.py"]