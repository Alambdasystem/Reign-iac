#!/usr/bin/env bash
set -euo pipefail
network="reign-smoke-${RANDOM}"
temporal="${network}-temporal"
computer="${network}-computer"
# These values are disposable local test credentials, not deployment credentials.
test_token="container-smoke-only-not-a-production-secret-0000"
cleanup() {
  docker rm -f "$computer" "$temporal" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker network create "$network" >/dev/null
docker run -d --name "$temporal" --network "$network" --network-alias temporal reign-temporal:ci >/dev/null
ready=false
for attempt in $(seq 1 60); do
  if docker exec "$temporal" temporal operator cluster health >/dev/null 2>&1; then ready=true; break; fi
  sleep 2
done
if [[ "$ready" != true ]]; then docker logs "$temporal"; exit 1; fi
docker run -d --name "$computer" --network "$network" \
  -e TEMPORAL_ADDRESS=temporal:7233 -e COMPUTER_TOKEN="$test_token" reign-computer:ci >/dev/null
ready=false
for attempt in $(seq 1 60); do
  if docker exec "$computer" python -c 'import os,urllib.request; r=urllib.request.Request("http://localhost:10000/healthz",headers={"Authorization":"Bearer "+os.environ["COMPUTER_TOKEN"]}); assert urllib.request.urlopen(r,timeout=5).status==200' >/dev/null 2>&1; then ready=true; break; fi
  sleep 2
done
if [[ "$ready" != true ]]; then docker logs "$computer"; exit 1; fi
docker exec -i "$computer" python - <<'PY'
import asyncio, urllib.request, urllib.error
from playwright.async_api import async_playwright
try:
    urllib.request.urlopen('http://localhost:10000/healthz', timeout=5)
except urllib.error.HTTPError as error:
    assert error.code == 401
else:
    raise AssertionError('Unauthenticated worker request was accepted')
async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=['--disable-dev-shm-usage'])
        page = await browser.new_page()
        await page.set_content('<title>REIGN container smoke</title><button>Ready</button>')
        assert await page.title() == 'REIGN container smoke'
        assert len(await page.screenshot()) > 100
        await browser.close()
asyncio.run(run())
print('PASS: Temporal RPC, worker readiness/auth, Chromium render and screenshot. Not full REIGN E2E.')
PY
