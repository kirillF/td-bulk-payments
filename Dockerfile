FROM ghcr.io/astral-sh/uv:0.13.1 AS uv
FROM python:3.15-slim
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY firm_payments ./firm_payments
RUN useradd --system --uid 10001 --no-create-home app
USER app
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "firm_payments.app:app", "--host", "0.0.0.0", "--port", "8000"]
