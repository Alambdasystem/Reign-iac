FROM reign-computer:ci
USER root
COPY services/core/requirements.txt /app/core-fixture-requirements.txt
RUN pip install --no-cache-dir -r /app/core-fixture-requirements.txt \
    && mkdir -p /fixture-vault /app/scripts \
    && chown reign:reign /fixture-vault \
    && chmod 700 /fixture-vault
COPY scripts/workspace_fixture.py /app/scripts/workspace_fixture.py
USER reign
ENV REIGN_VAULT_DIR=/fixture-vault PYTHONPATH=/app
CMD ["python", "/app/scripts/workspace_fixture.py"]
