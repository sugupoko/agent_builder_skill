# <agent_name> — production image
# Multi-stage build で依存とアプリを分離
FROM python:3.11-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ---
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY agent.py worker.py ./
COPY src/ ./src/
COPY config/ ./config/
COPY infra/cron/ ./infra/cron/

RUN useradd -m -u 1000 agent && chown -R agent:agent /app
USER agent

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -fs http://localhost:8001/health || exit 1

CMD ["python", "worker.py"]
