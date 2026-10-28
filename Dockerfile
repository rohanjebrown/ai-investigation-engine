# syntax=docker/dockerfile:1
FROM python:3.14.9-slim-bookworm@sha256:945c2676dc2b18e0c1f16879244122c57c0ffede60006bf3df159c507a31bcae AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.17@sha256:03bdc89bb9798628846e60c3a9ad19006c8c3c724ccd2985a33145c039a0577b /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /opt/app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src/ src/
RUN uv sync --locked --no-dev --no-editable
# This public, checksum-pinned 7 MB tokenizer is required for existing indexing.
# Download at build time only; no credentials, model weights, or runtime network.
RUN .venv/bin/python -m investigation_engine.infrastructure.tokenization.fetch_runtime /opt/tokenizer/tokenizer.json

# CI dependencies and fixtures never enter the final image.
FROM builder AS test
RUN uv sync --locked --group dev --no-editable
COPY tests/ tests/
COPY scripts/ scripts/
COPY data/samples/ data/samples/
COPY migrations/ migrations/
COPY deploy/ deploy/
COPY alembic.ini .env.example .python-version ./
ENV PATH="/opt/app/.venv/bin:$PATH"
CMD ["pytest", "tests/unit", "tests/integration", "-q"]

FROM python:3.14.9-slim-bookworm@sha256:945c2676dc2b18e0c1f16879244122c57c0ffede60006bf3df159c507a31bcae AS runtime
ENV PATH="/opt/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PORT=8080 HOME=/tmp TMPDIR=/tmp \
    CHUNKING_TOKENIZER_PATH=/opt/tokenizer/tokenizer.json
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home --home-dir /nonexistent app
WORKDIR /opt/app
COPY --from=builder /opt/app/.venv /opt/app/.venv
COPY --from=builder /opt/tokenizer/tokenizer.json /opt/tokenizer/tokenizer.json
COPY migrations/ migrations/
COPY alembic.ini ./
COPY deploy/entrypoint.py deploy/entrypoint.py
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 CMD ["/opt/app/.venv/bin/python", "-c", "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/health',timeout=2).read()"]
ENTRYPOINT ["/opt/app/.venv/bin/python", "/opt/app/deploy/entrypoint.py"]
