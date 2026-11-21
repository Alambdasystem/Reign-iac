FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=10000
WORKDIR /app
COPY services/ax/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home reign
COPY scripts/fetch_portrait_assets.py /app/scripts/fetch_portrait_assets.py
COPY services/ax/portrait-assets.json /app/services/ax/portrait-assets.json
RUN python /app/scripts/fetch_portrait_assets.py --output /app/ax_static/avatar-assets
COPY reign /app/reign
COPY services/ax/index.html services/ax/avatar-lab.html services/ax/team.html services/ax/genesis.html services/ax/nodes.html services/ax/guardrails.html services/ax/openstl.html services/ax/*.css services/ax/*.js /app/ax_static/
COPY services/ax/avatar-assets/sample-portrait.png services/ax/avatar-assets/THIRD_PARTY_NOTICES.txt /app/ax_static/avatar-assets/
COPY services/ax/team-avatar-assets /app/ax_static/team-avatar-assets/
USER reign
EXPOSE 10000
CMD ["sh", "-c", "exec uvicorn reign.ax:app --host 0.0.0.0 --port ${PORT:-10000}"]
