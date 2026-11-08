# Single-node development server with persistent SQLite: PILOT ONLY.
FROM python:3.12-slim-bookworm AS download
RUN python - <<'PY'
import hashlib, io, tarfile, urllib.request
url = 'https://github.com/temporalio/cli/releases/download/v1.10.0/temporal_cli_1.10.0_linux_amd64.tar.gz'
data = urllib.request.urlopen(url).read()
assert hashlib.sha256(data).hexdigest() == '521f461f3d108902763bb71e85cd0fd382dc9357b59604ae03da6ae734d16890'
with tarfile.open(fileobj=io.BytesIO(data)) as archive:
    member = next(m for m in archive.getmembers() if m.name in ('temporal', './temporal'))
    with open('/temporal', 'wb') as output:
        output.write(archive.extractfile(member).read())
PY
RUN chmod 755 /temporal

FROM debian:bookworm-slim
COPY --from=download /temporal /usr/local/bin/temporal
RUN useradd --uid 10001 --create-home temporal && mkdir /data && chown temporal:temporal /data
USER temporal
EXPOSE 7233
ENTRYPOINT ["temporal"]
CMD ["server", "start-dev", "--ip", "0.0.0.0", "--port", "7233", "--headless", "--db-filename", "/data/temporal.db"]
