# --- Build Stage ---
FROM python:3.13-slim AS builder

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Ensure output is not buffered
ENV PYTHONUNBUFFERED=1

# Copy dependency files first to leverage Docker cache
COPY pyproject.toml .

# Install build dependencies (gcc) needed for compiling C extensions like TgCrypto
RUN apt-get update && apt-get install -y --no-install-recommends gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies into a virtual environment using uv
RUN uv venv /opt/venv && \
    uv pip install --no-cache --python /opt/venv/bin/python -r pyproject.toml

# --- Final Stage ---
FROM python:3.13-slim

WORKDIR /app

# Ensure output is not buffered
ENV PYTHONUNBUFFERED=1

# Copy only the compiled virtual environment from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Add venv to PATH so we don't need to specify /opt/venv/bin/python everywhere
ENV PATH="/opt/venv/bin:$PATH"

# Copy application source code
COPY app/ app/
# We also create the data dir so it exists
RUN mkdir -p data/

# Expose the application port
EXPOSE 8000

# Start the application using uvicorn directly from the virtual environment
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
