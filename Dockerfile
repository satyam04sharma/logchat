# Historical Postgres API image, used only by compose.yaml.
# The public native release runs with logchat install / logchat start.
FROM python:3.12.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY api ./api
COPY cli ./cli
COPY pipeline ./pipeline
COPY connectors ./connectors
COPY logchat ./logchat
RUN pip install . && useradd --uid 10001 --create-home logchat
COPY scripts ./scripts
COPY supabase/migrations ./supabase/migrations
USER logchat
EXPOSE 8080
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
