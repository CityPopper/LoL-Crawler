FROM python:3.12-slim AS builder
WORKDIR /build

COPY lol-pipeline-common/ ./common/
RUN pip install --no-cache-dir ./common/

COPY lol-pipeline-delay-scheduler/pyproject.toml .
COPY lol-pipeline-delay-scheduler/src/ ./src/
RUN pip install --no-cache-dir .

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages \
                    /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY lol-pipeline-delay-scheduler/src/ ./src/

HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=60s \
    CMD python -c "import asyncio, os; from lol_pipeline.redis_client import get_redis, health_check; r=get_redis(os.environ.get('REDIS_URL', 'redis://redis:6379/0')); print(asyncio.run(health_check(r)))"

RUN adduser --disabled-password --no-create-home appuser
USER appuser

STOPSIGNAL SIGTERM

CMD ["python", "-m", "lol_delay_scheduler"]
