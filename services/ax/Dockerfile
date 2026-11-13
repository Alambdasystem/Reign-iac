FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=10000
WORKDIR /app
COPY services/ax/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home reign
COPY reign /app/reign
COPY services/ax/index.html services/ax/genesis.html services/ax/nodes.html services/ax/guardrails.html services/ax/*.css services/ax/*.js /app/ax_static/
USER reign
EXPOSE 10000
CMD ["sh", "-c", "exec uvicorn reign.ax:app --host 0.0.0.0 --port ${PORT:-10000}"]
