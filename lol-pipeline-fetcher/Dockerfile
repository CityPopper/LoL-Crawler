FROM python:3.14-slim AS builder
WORKDIR /build

COPY lol-pipeline-common/ ./common/
RUN pip install --no-cache-dir ./common/

COPY lol-pipeline-fetcher/pyproject.toml .
COPY lol-pipeline-fetcher/src/ ./src/
RUN pip install --no-cache-dir .

FROM python:3.14-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.14/site-packages \
                    /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=60s \
    CMD python -c "import asyncio, os, sys; from lol_pipeline.redis_client import get_redis, health_check; r=get_redis(os.environ.get('REDIS_URL', 'redis://redis:6379/0')); sys.exit(0 if asyncio.run(health_check(r)) else 1)"

RUN adduser --disabled-password --no-create-home appuser
USER appuser

STOPSIGNAL SIGTERM

CMD ["python", "-m", "lol_fetcher"]
