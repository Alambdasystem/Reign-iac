FROM mcr.microsoft.com/playwright/python:v1.64.1-noble
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=10000
WORKDIR /app
COPY services/computer/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home reign
COPY reign /app/reign
USER reign
EXPOSE 10000
CMD ["sh", "-c", "exec python -m uvicorn reign.computer:app --host 0.0.0.0 --port ${PORT:-10000}"]
