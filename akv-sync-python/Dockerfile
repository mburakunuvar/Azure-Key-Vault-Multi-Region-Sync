# ---------- build stage ----------
FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --target /app/deps -r requirements.txt

# ---------- runtime stage ----------
FROM python:3.12-slim

# Non-root user for security
RUN groupadd -r akvsync && useradd -r -g akvsync -d /app -s /sbin/nologin akvsync

WORKDIR /app

# Copy installed dependencies from builder
COPY --from=builder /app/deps /app/deps
ENV PYTHONPATH="/app/deps"

# Copy application
COPY akv_sync.py /app/akv_sync.py

# Drop to non-root
USER akvsync

ENTRYPOINT ["python", "-u", "/app/akv_sync.py"]
