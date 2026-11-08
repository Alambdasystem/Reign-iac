# UltraView AX server-side UI/API proxy. Build context: repository root.
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=10000
WORKDIR /app
COPY services/ax/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt \
    && useradd --uid 10001 --create-home reign
COPY reign /app/reign
USER reign
EXPOSE 10000
CMD ["sh", "-c", "exec python -m uvicorn reign.ax:app --host 0.0.0.0 --port ${PORT:-10000}"]
