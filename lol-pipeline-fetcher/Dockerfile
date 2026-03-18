FROM python:3.12-slim AS builder
WORKDIR /build

ARG COMMON_VERSION=main
COPY wheels/ ./wheels/
RUN if [ "$COMMON_VERSION" != "local" ]; then \
      pip install --no-cache-dir ./wheels/lol_pipeline_common-*.whl 2>/dev/null || \
      pip install --no-cache-dir \
        "lol-pipeline-common @ git+https://github.com/your-org/lol-pipeline-common.git@${COMMON_VERSION}"; \
    fi

COPY pyproject.toml .
COPY src/ ./src/
RUN if [ "$COMMON_VERSION" = "local" ]; then \
      pip install --no-cache-dir --no-deps .; \
    else \
      pip install --no-cache-dir .; \
    fi

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages \
                    /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY src/ ./src/

HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD python -c "import asyncio, os; from lol_pipeline.redis_client import get_redis, health_check; r=get_redis(os.environ.get('REDIS_URL', 'redis://redis:6379/0')); print(asyncio.run(health_check(r)))" 

CMD ["python", "-m", "lol_fetcher"]
