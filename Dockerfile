FROM python:3.12-slim

# Install system dependencies for processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application
COPY convert_to_hosts.py .

# Set up entrypoint
ENTRYPOINT ["python", "convert_to_hosts.py"]

CMD ["--help"]
