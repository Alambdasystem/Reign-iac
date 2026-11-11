#!/usr/bin/env bash
set -euo pipefail
network="reign-smoke-${RANDOM}"
temporal="${network}-temporal"
computer="${network}-computer"
core="${network}-core"
ax="${network}-ax"
vault_volume="${network}-vault"
fixture_volume="${network}-fixture-vault"
fixture_network="${network}-fixture"
certs="$(mktemp -d)"
evidence="${REIGN_SMOKE_OUTPUT:-$PWD/artifacts/smoke}"
mkdir -p "$evidence"
chmod 777 "$evidence"
# Disposable test credentials, never deployment credentials.
export COMPUTER_TOKEN="smoke-computer-only-not-production-000000"
export CORE_TOKEN="smoke-core-only-not-production-0000000000"
export AX_SESSION_KEY="smoke-session-only-not-production-0000000"
export AX_ADMIN_PASSWORD="smoke-admin-only-not-production-000000000"
export REIGN_VAULT_KEY="$(python3 -c 'import base64,hashlib; print(base64.b64encode(hashlib.sha256(b"REIGN disposable UI fixture vault key").digest()).decode())')"
export CREDENTIAL_CHANNEL_KEY="$(python3 -c 'import base64,hashlib; print(base64.b64encode(hashlib.sha256(b"REIGN disposable UI fixture channel key").digest()).decode())')"
cleanup() {
  result=$?
  if [[ "$result" != 0 ]]; then
    for name in "$ax" "$core" "$computer" "$temporal"; do docker logs --tail 60 "$name" 2>&1 || true; done
  fi
  docker rm -f "$ax" "$core" "$computer" "$temporal" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  docker network rm "$fixture_network" >/dev/null 2>&1 || true
  docker volume rm "$vault_volume" "$fixture_volume" >/dev/null 2>&1 || true
  rm -rf "$certs"
}
trap cleanup EXIT
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=ax -addext subjectAltName=DNS:ax -keyout "$certs/key.pem" -out "$certs/cert.pem" >/dev/null 2>&1
# Only this disposable test key is readable by the non-root AX container.
chmod 755 "$certs"
chmod 644 "$certs/"*.pem
docker network create "$network" >/dev/null
docker volume create "$vault_volume" >/dev/null
docker volume create "$fixture_volume" >/dev/null
# These cases run real Chromium inside the pinned Computer image. Missing browser
# binaries are a gate failure here, rather than a skipped local development test.
docker run --rm --network none --ipc=host -v "$PWD/tests:/app/tests:ro" -e REIGN_REQUIRE_BROWSER_TESTS=1 reign-computer:ci python -m unittest tests.test_browser_actions tests.test_browser_proxy -v
docker build -f scripts/workspace-fixture.Dockerfile -t reign-workspace-fixture:ci .
docker run -d --name "$temporal" --network "$network" --network-alias temporal reign-temporal:ci >/dev/null
docker run -d --name "$computer" --network "$network" --network-alias computer -e TEMPORAL_ADDRESS=temporal:7233 -e CORE_URL=http://core:10000 -e COMPUTER_TOKEN -e CREDENTIAL_CHANNEL_KEY reign-computer:ci >/dev/null
docker run -d --name "$core" --network "$network" --network-alias core -v "$vault_volume:/var/data/reign-vault" -e GENESIS_DRAFT_MODE=enabled -e REIGN_VAULT_DIR=/var/data/reign-vault -e REIGN_VAULT_KEY -e CREDENTIAL_CHANNEL_KEY -e TEMPORAL_ADDRESS=temporal:7233 -e COMPUTER_URL=http://computer:10000 -e CORE_TOKEN -e COMPUTER_TOKEN reign-core:ci >/dev/null
docker run -d --name "$ax" --network "$network" --network-alias ax -v "$certs:/test-tls:ro" -e CORE_URL=http://core:10000 -e AX_ALLOW_INTERNAL_HTTP=1 -e AX_PUBLIC_ORIGIN=https://ax:10000 -e CORE_TOKEN -e AX_SESSION_KEY -e AX_ADMIN_PASSWORD reign-ax:ci sh -c 'exec uvicorn reign.ax:app --host 0.0.0.0 --port 10000 --ssl-keyfile /test-tls/key.pem --ssl-certfile /test-tls/cert.pem' >/dev/null
wait_ready() {
  local ready=false
  for attempt in $(seq 1 60); do
    if docker exec "$core" python -c 'import os,ssl,urllib.request; r=urllib.request.Request("https://ax:10000/api/readyz",headers={"Authorization":"Bearer "+os.environ["CORE_TOKEN"]}); assert urllib.request.urlopen(r,context=ssl._create_unverified_context(),timeout=5).status==200' >/dev/null 2>&1; then ready=true; break; fi
    sleep 2
  done
  [[ "$ready" == true ]]
}
wait_ready
# The root entrypoint prepares the mount and relinquishes privileges before
# uvicorn starts. Check the actual service PID, not docker-exec's default user.
docker exec "$core" python -c 'from pathlib import Path; fields=dict(line.split(":",1) for line in Path("/proc/1/status").read_text().splitlines() if ":" in line); assert fields["Uid"].split()==["10001"]*4'
run_browser_test() {
  docker run --rm --network "$network" --ipc=host -v "$PWD/scripts:/smoke-scripts:ro" -v "$evidence:/evidence" -e CORE_TOKEN -e AX_ADMIN_PASSWORD -e SOURCE_SHA="${SOURCE_SHA:-${GITHUB_SHA:-local}}" -e SMOKE_PHASE="$1" reign-computer:ci python /smoke-scripts/browser_smoke.py
}
run_browser_test initial
# Exercise the actual private stdio MCP server from Core, plus Genesis's
# fail-closed draft-only policy. No model or real customer login is used.
docker exec "$core" python -m reign.mcp_acceptance | tee "$evidence/mcp-genesis-acceptance.log"
# Real authenticated AX/Genesis UI against disposable Core persistent disk.
# Never set GENESIS_DRAFT_MODE=enabled in production from this CI fixture.
docker run --rm --network "$network" --ipc=host -v "$PWD/scripts:/smoke-scripts:ro" -v "$evidence:/evidence" -e AX_ADMIN_PASSWORD -e SMOKE_PHASE=initial reign-computer:ci python /smoke-scripts/genesis_ui_smoke.py
docker restart "$temporal" "$computer" "$core" >/dev/null
wait_ready
run_browser_test restart
docker run --rm --network "$network" --ipc=host -v "$PWD/scripts:/smoke-scripts:ro" -v "$evidence:/evidence" -e AX_ADMIN_PASSWORD -e SMOKE_PHASE=restart reign-computer:ci python /smoke-scripts/genesis_ui_smoke.py
docker exec "$ax" python -m reign.live_acceptance --ci-local
# The controlled site must have no external network fallback, even if a browser
# regression bypasses request interception. Only Temporal is reachable here.
docker network create --internal "$fixture_network" >/dev/null
docker network connect --alias temporal "$fixture_network" "$temporal"
for phase in initial restart; do
  docker run --rm --network "$fixture_network" --ipc=host -v "$fixture_volume:/fixture-vault" -v "$evidence:/evidence" -e TEMPORAL_ADDRESS=temporal:7233 -e TEMPORAL_TASK_QUEUE="reign-workspace-fixture-${network}" -e SOURCE_SHA="${SOURCE_SHA:-${GITHUB_SHA:-local}}" -e SMOKE_PHASE="$phase" reign-workspace-fixture:ci
done
echo "PASS: four-service automated acceptance; real human acceptance remains pending."
