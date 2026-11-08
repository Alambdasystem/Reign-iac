# Application contracts and acceptance

Docker build context is the repository root; Dockerfiles live at
`services/{core,temporal,computer,ax}/Dockerfile`. Bind `0.0.0.0:$PORT` (10000)
except Temporal gRPC (7233). Keep containers independent, one replica each in
this pilot. Application releases must not add credentials to images or Git.

## Core and UltraView AX (implemented pilot; release gates still apply)

- Core consumes `TEMPORAL_ADDRESS`, namespace/task queue, `COMPUTER_URL`,
  `COMPUTER_TOKEN`, `CORE_TOKEN`. Use server-side authenticated requests.
- AX consumes `CORE_URL`, `CORE_TOKEN`, `AX_SESSION_KEY`. AX owns an authenticated
  browser session; service tokens must not be shipped in JavaScript, localStorage,
  URL query strings, screenshots or logs. Define a proper user login before
  exposing session actions. Use secure HttpOnly cookies and CSRF protection.
- AX `GET /healthz` returns `{ "status": "ok" }` for process readiness only.
- AX `GET /api/readyz`, authenticated by Bearer `CORE_TOKEN` for CI, checks actual
  dependencies and returns `{ "status": "ready", "components": {
  "core":"ready", "temporal":"ready", "computer":"ready" } }` only after
  a real Temporal RPC and authenticated worker readiness check succeed. Return
  503 on dependency failure, 401 without authentication. Do not hardcode success.
- Session actions (open, navigate, frame, takeover, input, handback, close) go
  through Core. Tie each session and frame to the authenticated human owner.
  Establish endpoint schemas before implementing the AX client and its tests.

## Computer (experimental implementation exists)

The `reign/` code starts a Temporal worker and a private FastAPI service. It
accepts a single browser session, restricts HTTP to the exact Git High HTTPS
hostname, blocks WebSockets/downloads/popups, and has ownership epochs for human
input. Audit and test it before a release; a passing container smoke test is not
a full correctness/security assessment. This is not a general untrusted-code
sandbox or a multi-tenant browser service.

Current private endpoints require Bearer `COMPUTER_TOKEN`: `/healthz`,
`/sessions/{id}`, `/sessions/{id}/frame`, `/sessions/{id}/control`,
`/sessions/{id}/input`, and DELETE `/sessions/{id}`.

## E2E acceptance (automated CI plus separate human signoff)

The four-container smoke test exercises the real AX UI, Core, Temporal and Computer
against public Git High, including frame freshness, stale commands, cleanup and
restart recovery. It records JSON evidence and a screenshot. It does not replace
the following deployed acceptance or James's human signoff. Frames carry
`X-Session-ID`, `X-Frame-Sequence` and `X-Frame-Timestamp` through Core and AX.

1. Use the deployed AX URL in a real Playwright browser; authenticate James.
2. Submit **Open Git High** through AX. Capture the Core request/session ID and
   the actual Temporal workflow ID/run history and successful Computer activity.
3. Assert the worker reached `https://alambda-forge.onrender.com/`, valid HTTP
   status and rendered page content. Confirm fresh frames in AX, with changing
   frame sequence/timestamps; a cached screenshot is insufficient.
4. James clicks **Take control**. Record the control owner/epoch. Perform a safe
   real human click/scroll and observe the change in live frames. During takeover,
   queued/stale agent actions must be rejected. Do not enter private credentials
   while recordings are active.
5. James chooses **Return control**. Verify stale human input is rejected and a
   new authorized agent action succeeds. Automated takeover tests complement,
   but do not replace, James's observed human acceptance.
6. Close the session; verify browser resources are released. Restart Computer
   and verify honest expired-session behavior. Restart Temporal and verify
   persisted workflow history without duplicating browser actions.
7. Store redacted screenshots/trace, timestamps, image digests, workflow IDs,
   checks and failures. Report passed/failed/blocked separately for deployment,
   browser behavior, live viewing and human acceptance.
