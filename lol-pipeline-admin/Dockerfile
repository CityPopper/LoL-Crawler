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

# Admin is a one-shot container — no HEALTHCHECK
CMD ["python", "-m", "lol_admin"]
