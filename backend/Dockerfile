ARG SCRAPLING_BASE_IMAGE=pyd4vinci/scrapling:latest
FROM ${SCRAPLING_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir "uv>=0.5.0" \
    && uv sync --frozen --no-dev --no-install-project

COPY app ./app

EXPOSE 8765
CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8765"]
