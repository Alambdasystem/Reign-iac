#!/usr/bin/env bash
set -euo pipefail
network="reign-smoke-${RANDOM}"
temporal="${network}-temporal"
computer="${network}-computer"
core="${network}-core"
ax="${network}-ax"
certs="$(mktemp -d)"
evidence="${REIGN_SMOKE_OUTPUT:-$PWD/artifacts/smoke}"
mkdir -p "$evidence"
chmod 777 "$evidence"
# Disposable test credentials, never deployment credentials.
export COMPUTER_TOKEN="smoke-computer-only-not-production-000000"
export CORE_TOKEN="smoke-core-only-not-production-0000000000"
export AX_SESSION_KEY="smoke-session-only-not-production-0000000"
export AX_ADMIN_PASSWORD="smoke-admin-only-not-production-000000000"
cleanup() {
  result=$?
  if [[ "$result" != 0 ]]; then
    for name in "$ax" "$core" "$computer" "$temporal"; do docker logs --tail 60 "$name" 2>&1 || true; done
  fi
  docker rm -f "$ax" "$core" "$computer" "$temporal" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -rf "$certs"
}
trap cleanup EXIT
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=ax -addext subjectAltName=DNS:ax -keyout "$certs/key.pem" -out "$certs/cert.pem" >/dev/null 2>&1
# Only this disposable test key is readable by the non-root AX container.
chmod 755 "$certs"
chmod 644 "$certs/"*.pem
docker network create "$network" >/dev/null
docker run -d --name "$temporal" --network "$network" --network-alias temporal reign-temporal:ci >/dev/null
docker run -d --name "$computer" --network "$network" --network-alias computer -e TEMPORAL_ADDRESS=temporal:7233 -e COMPUTER_TOKEN reign-computer:ci >/dev/null
docker run -d --name "$core" --network "$network" --network-alias core -e TEMPORAL_ADDRESS=temporal:7233 -e COMPUTER_URL=http://computer:10000 -e CORE_TOKEN -e COMPUTER_TOKEN reign-core:ci >/dev/null
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
run_browser_test() {
  docker run --rm --network "$network" --ipc=host -v "$PWD/scripts/browser_smoke.py:/test.py:ro" -v "$evidence:/evidence" -e CORE_TOKEN -e AX_ADMIN_PASSWORD -e SOURCE_SHA="${SOURCE_SHA:-${GITHUB_SHA:-local}}" -e SMOKE_PHASE="$1" reign-computer:ci python /test.py
}
run_browser_test initial
docker restart "$temporal" "$computer" >/dev/null
wait_ready
run_browser_test restart
echo "PASS: four-service automated acceptance; real human acceptance remains pending."
