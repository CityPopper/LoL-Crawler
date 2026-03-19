FROM python:3.12-slim AS builder
WORKDIR /build

COPY lol-pipeline-common/ ./common/
RUN pip install --no-cache-dir ./common/

COPY lol-pipeline-ui/pyproject.toml .
COPY lol-pipeline-ui/src/ ./src/
RUN pip install --no-cache-dir .

FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages \
                    /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY lol-pipeline-ui/src/ ./src/

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/')"

RUN adduser --disabled-password --no-create-home appuser
USER appuser

STOPSIGNAL SIGTERM

CMD ["python", "-m", "lol_ui"]
