# Application contracts and acceptance

Docker build context is the repository root; Dockerfiles live at
`services/{core,temporal,computer,ax}/Dockerfile`. Bind `0.0.0.0:$PORT` (10000)
except Temporal gRPC (7233). Keep containers independent, one replica each in
this pilot. Application releases must not add credentials to images or Git.

## Core and UltraView AX (implemented pilot; release gates still apply)

UltraView now uses the approved v1 visual source recorded in `docs/design/`.
`GET /api/me` checks the human session and returns explicit capability flags.
`GET /api/status` uses that session to request real Core dependency readiness.
Neither exposes Core/Computer credentials. The older Bearer `/api/readyz` remains
available for deployment verification. The text composer supports explicit
workspace commands and LangGraph conversations. The voice implementation uses
WebRTC Realtime transport with mandatory delegation into that same LangGraph
conversation. See [VOICE.md](VOICE.md) for the contract and the separate provider,
browser, and physical-device acceptance requirements.

- Core consumes `TEMPORAL_ADDRESS`, namespace/task queue, `COMPUTER_URL`,
  `COMPUTER_TOKEN`, `CORE_TOKEN`. Use server-side authenticated requests.
- AX consumes `CORE_URL`, `CORE_TOKEN`, `AX_SESSION_KEY`. AX owns an authenticated
  browser session; service tokens must not be shipped in JavaScript, localStorage,
  URL query strings, screenshots or logs. Define a proper user login before
  exposing session actions. Use secure HttpOnly cookies and CSRF protection.
- `GET /api/voice` reports sanitized provider configuration. `configured` means
  server configuration is present, not that a provider connection has passed.
  `POST /api/voice/offer` validates and brokers an SDP offer with session/CSRF
  authorization in AX and service authorization in Core. Permanent OpenAI
  credentials remain in Core. Voice tool calls use the existing `/api/chat`
  routes, thread/session identity, and takeover fencing.
- AX `GET /healthz` returns `{ "status": "ok" }` for process readiness only.
- AX `GET /api/readyz`, authenticated by Bearer `CORE_TOKEN` for CI, checks actual
  dependencies and returns `{ "status": "ready", "components": {
  "core":"ready", "temporal":"ready", "computer":"ready" } }` only after
  a real Temporal RPC and authenticated worker readiness check succeed. Return
  503 on dependency failure, 401 without authentication. Do not hardcode success.
- Session actions (open, navigate, frame, takeover, input, handback, close) go
  through Core. Tie each session and frame to the authenticated human owner.
  Establish endpoint schemas before implementing the AX client and its tests.

### Browser session recovery

This pilot has one authenticated administrator and one active managed browser.
`GET /api/sessions/active` requires the AX login session (Core and Computer use
their existing service authorization) and returns `{"session":null}` or the
active session's public metadata. Responses are not cached. This discovery
contract must be replaced with per-owner filtering before adding multiple users.

On sign-in, AX first checks the tab's stored `reign-session` identifier. A missing,
invalid, or confirmed expired identifier triggers active-session discovery.
Recovery attaches a fresh live frame to the existing browser, preserving its
page, URL, control owner, and epoch. It does not open, navigate, close, or take
control of that session. Workspace actions stay disabled until restoration
finishes. A known `browser_occupied` HTTP 409 triggers read-only discovery and an
explicit explanation; the failed open request is never replayed automatically.

Conversation restoration is separate. If a stored `reign-thread` refers to a
confirmed expired browser and the run has finished, AX clears only the tab's
local conversation reference and explains that the next request starts a new
conversation. It sends no delete or cancel request. Running or paused threads
remain tracked; recovery never cancels another tab's graph work. Existing
ownership/epoch checks still govern all subsequent actions.

Acceptance must cover a second signed-in tab with empty session storage, an
expired stored session, preserved human takeover, fresh frames, and absence of
session mutations during discovery. These checks are distinct from deployed
voice and physical-device acceptance.

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


## LangChain / LangGraph agent

Core exposes authenticated `GET /api/agent`, `POST /api/chat`,
`GET /api/chat/{thread_id}`, `POST /api/chat/{thread_id}/resume`, and
`DELETE /api/chat/{thread_id}`. AX forwards these only with its signed login
cookie; writes also require same-origin CSRF verification. The OpenAI key never
reaches AX or the browser. `/api/agent.configured` is the configuration signal;
`/api/me.capabilities.ai_chat` identifies endpoint support, not provider readiness.

Chat uses LangChain ChatOpenAI with the OpenAI Responses API, `store=false`,
serial tool calls and Workmates' configured model. LangGraph maintains messages,
conditional model/tool execution, checkpoints, and human-control interrupts.
Production Core starts a fixed private stdio MCP server using the official MCP
Python SDK. It verifies the discovered catalog against the same Pydantic schemas
used to bind LangChain StructuredTools. LangGraph invokes that connection;
Playwright runs in Computer through Temporal. The model cannot select a server,
command, owner, session or control epoch. The catalog consists of:

- `workspace_status`, `browser_status`, `open_browser`, `navigate_browser`;
- `browser_observe`, `browser_click`, `browser_fill`, `browser_press`, `browser_scroll`;
- `vault_status`, `vault_list_accounts`, `vault_request_account`,
  `vault_prepare_signup`, `vault_use_account`.

Only the request-bound session can be used. URL allowlisting and Temporal/worker
epoch fencing remain enforced. The tools do not provide arbitrary selectors,
JavaScript evaluation, shell commands, server registration or database access.
Playwright is the implementation behind this restricted MCP catalog, not an
unrestricted public Playwright MCP server.

Runs are asynchronous and expose actual node/tool events, text results and token
usage. No hidden reasoning or raw provider exceptions appear in traces. Return
control and explicitly resume to continue an interrupted navigation. Stop/new
chat cancels and deletes the conversation; it does not undo completed actions or
close the browser. Browser controls remain responsive during model requests.

Limits: sixteen tool steps per turn, 480 seconds per run, twelve turns per thread,
eight threads, two active runs, and thirty minutes idle lifetime. Checkpoints are
in memory on the single Core instance and reset on restart. Saved accounts use
the independent durable vault described below; this does not persist chats or
browser cookies. Durable PostgreSQL checkpoints remain pending restricted REIGN
DB access.

## Interactive browser actions

An observation contains bounded visible text and element references, never input
values. References expire after 60 seconds and are bound to the document, URL,
DOM state and control epoch. Each effect consumes its observation before acting.
A changed page or takeover requires a new observation. Generic fill rejects
password, OTP, token, hidden and file fields; Core performs a no-text preflight
before putting ordinary text into a Temporal action payload. The worker checks
again under its session lock immediately before the effect.

Browser activities have one attempt. An unknown action outcome halts the
graph turn instead of repeating a click or submission. Human verification,
credential reveal, account-registration submission and terms acceptance stop
for Take control. Sign-in and requested repository form submission can proceed
when their controls are unambiguous; success must be verified from a subsequent
observation. No tool treats filled credentials as an authenticated login.

Native Playwright routing groups HTTP redirects into one chain, so routing alone
is not the network boundary. Every managed context also uses a private loopback
CONNECT proxy that permits only approved HTTPS host/port pairs, including native
redirect connections. Credential injection narrows that proxy to the credential
origin and closes incompatible existing/in-flight tunnels. Chromium's implicit
loopback proxy bypass, HTTP/2 coalescing, QUIC and non-proxied WebRTC UDP are disabled. TLS stays
between the browser and website; the proxy never decrypts or records page data.
The existing BROWSER_PROXY override is rejected instead of replacing this gate.

Stop/end-call cancellation crosses the standard MCP cancellation notification
into the uniquely owned Temporal workflow. Cleanup is bounded and never cancels
another operation or retries the original effect. Already-dispatched browser
actions may complete; cancellation does not establish that an action was undone.

## Secure saved accounts

AX retains only Chat and Work navigation. Work includes an Accounts dialog with
Add, Use saved login, and Remove. The agent can request that secure dialog; it
cannot accept a password as a tool argument. Opening the dialog mutes an active
microphone and prevents starting or unmuting voice until it closes. Closing it
does not unmute automatically. Inputs are cleared after capture, closure,
authentication loss and page exit. Passwords do not enter chat or browser storage.

Authenticated account endpoints are `GET/POST /api/accounts`,
`DELETE /api/accounts/{id}` and `POST /api/accounts/{id}/use`. AX enforces the
existing signed cookie and same-origin CSRF rules, bounded bodies, no redirects
and fixed errors. Core derives the single administrator identity server-side.
Metadata returned to AX includes username; model metadata omits it. Duplicate
labels never silently overwrite credentials. Remove deletes only the vault
entry, not the external website account. Use saved login joins the same graph
thread and execution lease used by text and voice.

Vault persistence requires an actual dedicated mounted directory specified by
`REIGN_VAULT_DIR` and a separately managed base64 256-bit `REIGN_VAULT_KEY`.
Missing disk, invalid key, wrong key, corruption or unsafe filesystem objects
fail closed. There is no ephemeral fallback, automatic key generation or shared
database migration. AES-GCM authenticates encrypted account fields and their
record bindings. SQLite files are 0600 beneath a 0700 directory. Core's fixed-path
entrypoint prepares mount ownership and drops root before starting the service.

The model receives account IDs. A trusted MCP handler issues a one-use, 60-second
grant bound to account revision, owner, browser session, observation, origin and
epoch. Only that reference enters Temporal. Computer redeems it at the private
Core endpoint with its service authorization; the response is encrypted using a
separate `CREDENTIAL_CHANNEL_KEY` and authenticated context. The worker fills one
unambiguous, same-origin POST form under its control lock. It does not submit the
form as part of credential injection. Secret-bearing sessions restrict all
requests to the credential origin. Screenshots mask form fields and known
password echoes; public URLs omit queries/fragments, and model observations
redact known passwords, including normalized whitespace and encoded variants.

`vault_prepare_signup` creates a generated private credential in the vault,
returning metadata plus `external_account_created:false`. Required registration
submission, terms, MFA, CAPTCHA, passkeys and recovery still use human takeover.
Outlook and Microsoft Graph are deferred at James's request. The existing exact
Git High hostname restriction remains in place.

Acceptance distinguishes real browser/transport integration against a controlled
fixture with a scripted model, public Git High navigation, real provider tests,
and actual external account/repository creation. CI also tests Accounts UI,
one-use and wrong-context grants, secret exclusions, and account persistence
across new service/container and browser instances. Controlled fixture success
does not assert that a live external account or repository was created.
