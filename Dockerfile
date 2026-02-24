FROM python:3.13-slim

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Ensure output is not buffered
ENV PYTHONUNBUFFERED=1

# Copy dependency files first to leverage Docker cache
COPY pyproject.toml .

# Install dependencies using uv
RUN uv pip install --system --no-cache -r pyproject.toml

# Copy application source code
COPY app/ app/
# We also create the data dir so it exists
RUN mkdir -p data/

# Expose the application port
EXPOSE 8000

# Start the application using uvicorn via uv
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
