# Multi-stage build: builder installs, runtime stays slim and non-root.
FROM python:3.14-slim AS builder
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres,anthropic,otel]"

FROM python:3.14-slim
# Never run as root in production containers.
RUN useradd --create-home --uid 10001 relay
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
USER relay
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/healthz')"
CMD ["uvicorn", "relay.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
