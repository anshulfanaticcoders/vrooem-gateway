FROM python:3.11-slim AS base

WORKDIR /app

# System dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry==1.8.4

# Copy dependency files first (better Docker cache)
COPY pyproject.toml poetry.lock* ./

# Install dependencies (no dev dependencies in production)
RUN poetry config virtualenvs.create false && \
    poetry install --no-interaction --no-ansi --no-root --only main

# Copy application code
COPY . .

# Install the project itself
RUN poetry install --no-interaction --no-ansi --only-root

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
