FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY protocol /app/protocol
COPY server /app/server
RUN pip install --no-cache-dir /app/protocol /app/server

WORKDIR /app/server
CMD ["uvicorn", "adbcaller_server.main:app", "--host", "0.0.0.0", "--port", "8000"]
