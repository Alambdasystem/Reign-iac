FROM tailscale/tailscale:v1.104.7 AS tailscale

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=10000
# Also honored by the temporary diagnostic command's direct Uvicorn CLI.
ENV UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN=2
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates openssh-client \
    && rm -rf /var/lib/apt/lists/*
COPY --from=tailscale /usr/local/bin/tailscale /usr/local/bin/tailscaled /usr/local/bin/
COPY services/core/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && (getent group 1000 || groupadd --gid 1000 render-secrets) \
    && useradd --uid 10001 --create-home --groups 1000 reign
COPY reign /app/reign
# A runtime disk can arrive owned by root. The small fixed-path entrypoint fixes
# only that mount's ownership and drops to uid/gid 10001 before starting Core.
ENTRYPOINT ["python", "-m", "reign.core_entrypoint"]
EXPOSE 10000
CMD ["python", "-m", "reign.tailscale_runtime", "sh", "-c", "exec uvicorn reign.core:app --host 0.0.0.0 --port ${PORT:-10000}"]
