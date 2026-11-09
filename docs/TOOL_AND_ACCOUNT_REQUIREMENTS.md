# Interactive tools and saved accounts

Implementation merged and exact-source release tested, 2026-11-09. Activation
awaits the reviewed Blueprint and added-storage cost approval. Deployment and
external-site acceptance must be recorded separately. See the
[release receipt](deployments/2026-11-09-browser-vault-release.md). James confirmed voice works on his device. The
earlier cloud browser's missing microphone is a historical test limitation.
Outlook is deferred until a deliberate browser-login or Microsoft Graph task.
“SCP” in the latest request is interpreted as MCP from context.

## What was missing, and what changed

Playwright was already installed and used by Computer, but the model only had
status, open and navigation tools. The earlier MCP configuration was a scaffold,
not a connected interactive tool path. Production Core now uses a real private
stdio MCP server, official MCP SDK, LangChain StructuredTools and the existing
LangGraph loop. Computer executes Playwright actions through Temporal.

Top navigation now contains **Chat** and **Work**. The old Agents tab requested
dependency readiness; Tools showed configuration information. Neither managed
agents or connectors. Those tabs and their bottom shortcuts are removed.
**Accounts** is a secure dialog within Work.

| Capability | Implementation |
| --- | --- |
| Inspect pages | Bounded visible text and current element references; no input values |
| Click, fill ordinary text, press keys, scroll | Fresh targets, session/epoch fencing, bounded actions |
| Save an existing login | Authenticated Accounts form and encrypted server-side vault |
| Reuse a login | Account ID and one-use grant; trusted worker fills credentials |
| Prepare signup | Generate a private password in the vault; no secret in model output |
| Finish signup | Human completes final submission, consent or verification; agent observes the result |
| Create a repository | Requested name/visibility, ordinary form actions, resulting-page verification |
| Voice requests | Same graph, browser session and MCP tools as typed requests |
| Outlook / Graph | Deferred; no Microsoft connection or broader destination policy |

The permitted destination remains exactly `https://alambda-forge.onrender.com`.
More sites require a separately tested destination/redirect policy. External
desktop adapters and arbitrary MCP server registration remain unsupported.

## User flow

1. Open Work → Accounts → Add account. Enter the label, permitted HTTPS origin,
   username and password there. Voice mutes while the dialog is open and does
   not automatically unmute when it closes.
2. Save. Success means the encrypted vault write completed. Inputs clear after
   capture, closure, authentication loss and page exit. No password is placed
   in chat, localStorage or sessionStorage.
3. Open the site's login page and choose Use saved login, or ask “Sign into Git
   High using my saved account.” The agent selects an account ID. The worker
   fills an unambiguous same-origin credential form through a private channel.
4. The agent can click an ordinary Sign in control and inspect the next page.
   MFA, CAPTCHA, passkeys, terms and account recovery require Take control.
   Return control, then resume or give the next instruction.
5. Remove deletes only the saved credential. It does not delete the website
   account or revoke existing browser sessions.

An exact repeat save is idempotent. Changed credentials under an existing label
are rejected, preserving the old record. Deliberate replacement currently means
removing that entry and adding the correct one. Passwords aren't requested
through chat or dictation.

`vault_prepare_signup` generates and saves a private password for an explicitly
requested new account and returns `external_account_created:false`. Credential
preparation is separate from completing the site's registration. This version
has no generic password-reveal or export tool.

## State and security boundaries

| State | Lifetime |
| --- | --- |
| REIGN access | AX secure HttpOnly administrator session |
| Saved website credentials | Encrypted Core vault, when a real durable mount and key are configured |
| Website cookies | Computer browser context; lost when it closes or the worker restarts |
| Conversation | In-memory LangGraph checkpoints; lost when Core restarts |

ChatGPT's cloud browser and REIGN's managed browser are separate. Logging into
one does not copy credentials or cookies into the other. Saving an account lets
REIGN sign in again in a new browser; site verification can still be required.

The fourteen tool schemas share one catalog and are verified during MCP startup.
Model arguments cannot select a server command, owner, session or control epoch.
Observations expire after 60 seconds and bind references to the document, URL,
DOM state and epoch. Each effect consumes its observation. Generic fill rejects
credential fields, with a no-text worker preflight before ordinary text enters
Temporal. Unknown outcomes halt the graph instead of repeating a submission.

Core assigns the authenticated owner. Model account metadata contains IDs,
labels and origins, without usernames or passwords. One-use grants bind account
revision, owner, origin, session, observation and epoch. Only grant references
enter Temporal. Computer redeems them through a private, authenticated Core
endpoint and receives an AES-GCM response using a dedicated channel key, separate
from the bearer token. It fills under the control lock without submitting.

After injection, requests are restricted to the credential origin. Public URLs
omit queries/fragments. Observations redact known passwords and encoded or
whitespace-normalized variants. Screenshots conservatively mask all form fields
and known password echoes. Filled credentials are never proof of authentication.
Full contracts are in [APPLICATION_CONTRACTS.md](APPLICATION_CONTRACTS.md).

Redirect protection also runs below Playwright's page-routing API, using a
per-context loopback CONNECT proxy. It checks the allowed HTTPS destination on
every new connection and closes incompatible tunnels before credential entry.
This keeps native login redirects functional while preventing a redirected
credential POST from reaching another site. Stop cancellation reaches the
operation's own Temporal workflow; it cannot undo an action already sent.

## Durable storage and deployment

The proposed vault uses AES-GCM encrypted SQLite records on a dedicated **1 GB
Core disk**, mounted at `/var/data/reign-vault`. `REIGN_VAULT_KEY` is generated
and held separately in Render configuration. A separate generated
`CREDENTIAL_CHANNEL_KEY` is referenced by Core and Computer. Missing storage,
wrong keys, corruption and unsafe filesystem objects fail closed without
replacing data. There is no ephemeral fallback or shared database migration.

Render documents disk storage at **$0.25 per GB per month**. The proposed addition
would bring the approved ~$64.25/month base pilot to ~$64.50/month, excluding
the existing variable charges. Core remains single-instance and has a brief
stop/start interruption during deploys. The repository's cost approval rule
applies before adding the disk; prepare tested image pins and the exact existing
Blueprint plan before requesting that final approval.

The disk is unavailable during pre-deploy commands, so the voice provider gate
does not depend on it. Core's entrypoint prepares only the fixed mount directory
and drops to its dedicated service user. Changing the encryption key without
re-encrypting records makes the vault unavailable. Preserve the matching key and
records. Application-consistent SQLite backups and a restore drill remain an
operational follow-up; filesystem snapshots alone aren't a tested restore.

## Workmates and Outlook

The prior audit inspected Workmates API source
`15b268a0502bc983a8ad835d8707e7d7148163e9`, including
[Drive OAuth](https://github.com/Alambdasystem/workmates-api/blob/15b268a0502bc983a8ad835d8707e7d7148163e9/drive.py).
It ties OAuth state to an admin session and persists encrypted tokens. That
informed the server-owned credential boundary; it wasn't an existing generic
website vault or Graph connection. Workmates code, secrets and data are unchanged.

James deferred Outlook. A future Graph connection should use delegated Microsoft
authorization, PKCE, an exact registered callback, encrypted per-account token
cache and incremental scopes. A Graph connection and an Outlook browser login
are separate capabilities. Neither is claimed here.

## Acceptance boundaries

Unit/protocol tests cover the actual MCP SDK, account authentication, encryption,
duplicate preservation, one-use and wrong-context grants, graph halts and
takeover. Pinned real Chromium cases cover DOM/target changes, field restrictions,
same-origin credential forms and masking. Four-service CI retains public Git High
navigation and adds Accounts UI save/list/remove, secret clearing, microphone
interlock and credential metadata persistence after Core restart.

A separate controlled website and scripted model exercise real LangGraph → MCP
→ Core → Temporal → Playwright → vault. They verify login, private repository
creation, takeover/handback, secret exclusions from workflow histories/model
messages, and saved-login reuse in a fresh container/browser. Receipts explicitly
label the fixtures; they do not claim a live external account/repository was
created or a live provider/microphone was used. Deployed acceptance needs its own
evidence after promotion.

## Primary references

- [Render persistent disks](https://render.com/docs/disks)
- [Render Blueprint specification](https://render.com/docs/blueprint-spec)
- [Render storage pricing](https://render.com/articles/how-much-does-cloud-application-hosting-cost-for-small-businesses)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Playwright authentication](https://playwright.dev/python/docs/auth)
- [Microsoft authorization-code flow and PKCE](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
