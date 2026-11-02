FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.10.11
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"

WORKDIR /repo
COPY pyproject.toml uv.lock ./
COPY services/api services/api
RUN uv sync --frozen --no-dev --no-editable --package gbfs-api

WORKDIR /repo/services/api
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
