FROM python:3.12-slim AS builder
WORKDIR /build

COPY lol-pipeline-lcu/pyproject.toml .
COPY lol-pipeline-lcu/src/ ./src/
RUN pip install --no-cache-dir .

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages \
                    /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY lol-pipeline-lcu/src/ ./src/

# LCU is a one-shot tool — no HEALTHCHECK

RUN adduser --disabled-password --no-create-home appuser
USER appuser

STOPSIGNAL SIGTERM

CMD ["python", "-m", "lol_lcu"]
