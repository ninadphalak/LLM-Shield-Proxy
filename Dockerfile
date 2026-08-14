# Multi-stage security-hardened non-root Dockerfile for LLM-Shield-Proxy
# Stage 1: Build Dependencies
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt pyproject.toml README.md ./
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Production Distroless-style Non-Root Runtime
FROM python:3.12-slim AS runner

WORKDIR /app

# Create non-root system user and group
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -d /app -s /bin/false appuser

# Copy installed Python dependencies from builder
COPY --from=builder --chown=appuser:appgroup /root/.local /app/.local

# Set environment paths
ENV PATH="/app/.local/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Copy application source code
COPY --chown=appuser:appgroup ./llm_shield_proxy ./llm_shield_proxy

# Switch to unprivileged user
USER 10001

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

ENTRYPOINT ["uvicorn", "llm_shield_proxy.main:app", "--host", "0.0.0.0", "--port", "8000"]
