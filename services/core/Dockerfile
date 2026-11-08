FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=10000
WORKDIR /app
COPY services/core/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home reign
COPY reign /app/reign
USER reign
EXPOSE 10000
CMD ["sh", "-c", "exec uvicorn reign.core:app --host 0.0.0.0 --port ${PORT:-10000}"]
