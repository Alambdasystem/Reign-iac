# Prompt tools and reusable accounts

Status: current capability audit and proposed follow-on design, 2026-11-09 UTC.
This document does not claim that account storage, Outlook, or autonomous form
interaction has been implemented. The accompanying session-recovery change
addresses a separate failure found during the live prompt test.

## What the four tabs do today

| View | Implemented behavior |
| --- | --- |
| Chat | Shared activity feed, text composer, live voice controls, and managed browser view. Ordinary prompts use LangGraph. |
| Work | The same browser workspace plus information about the configured Git High destination. No project/repository manager. |
| Agents | A live readiness request for Core, Temporal, and Computer. No agent creation, selection, or scheduling UI. |
| Tools | Capability/configuration information. No connector setup, credential manager, or MCP registration UI. The bottom MCP button selects this view. |

The model is bound to four tools: `workspace_status`, `browser_status`,
`open_browser`, and `navigate_browser`. It can obtain readiness and current
browser metadata, open an allowed site, and navigate an agent-controlled browser.
It cannot yet read page contents, click or fill forms as an agent, or create an
account/repository. Manual takeover separately provides click, text, key and
scroll controls. Configuration labels are not proof of an external-site login.

Typed quick commands can bypass the model. Live prompt acceptance must therefore
use ordinary sentences and verify actual tool events and resulting page state.
Native voice delegates to the same LangGraph tools. Keyboard dictation into the
composer is the normal text path; it does not verify the Realtime microphone
transport. Spoken requests do not gain extra tools from frontend quick commands.

Sources: [agent.py](../reign/agent.py), [live.js](../services/ax/live.js),
[computer.py](../reign/computer.py).

## Live findings that motivated session recovery

The authenticated workspace-status prompt succeeded. An initial `open_browser`
prompt failed because the worker's one browser slot was already occupied, while
the current AX tab had lost its session reference. The worker rejected the open
with 409; Core incorrectly reported 503. After the earlier session expired, a
single retry successfully opened Git High and `browser_status` reported the
actual GITHI page and agent control. An Outlook prompt was rejected by the
configured URL policy. No external credentials were entered and no account or
repository was created.

The recovery change discovers the existing browser only after authentication in
this single-admin pilot. It preserves the current page, mode and epoch, keeps
the one-session limit, and reports occupied sessions distinctly. It does not
implement persistent login or a multi-user account boundary. Deployment and
final acceptance must be recorded separately from source/test completion.

## Three separate kinds of authentication

1. **REIGN access:** the workspace password authenticates the person to AX. It
   does not log the managed browser into another website.
2. **Website login:** a username/password belongs to a particular account and
   permitted site. Reusing it requires a real credential store and a controlled
   injection path.
3. **Signed-in website state:** cookies and tokens live inside the managed
   browser context. Current contexts are ephemeral. Restoring an active context
   is different from persisting its login across closure or service restart.

ChatGPT's cloud browser and REIGN's Computer container are separate browsers.
Signing into one does not transfer credentials or cookies into the other. The
existing `ComputerProfile.secret_ref` is only a validated reference contract;
there is no resolver or vault behind it yet.

## Proposed user flow

Provide an **Accounts & connections** area under Tools with account labels,
permitted sites, connection state, last successful use, reconnect and remove.
The following controls are requirements, not existing UI:

- **Connect Outlook:** use Microsoft's account chooser and sign-in/consent flow.
- **Add website login:** collect credentials in a dedicated secure form and save
  them to the configured vault. Never collect them through chat or dictation.
- **Use saved account:** a prompt such as “Open Git High with my saved account”
  supplies an opaque account reference to the trusted executor. The model gets
  the account label and success/failure, not the password.
- **Create account:** open the requested site's registration flow, complete any
  required human verification/terms steps, and confirm the resulting account.
  Report the login as saved only after the vault write succeeds.
- **Create repository:** require a working browser-action or Git service tool,
  the destination account, repository name and visibility. Verify the created
  repository and return its actual URL.

MFA, passkey approvals, CAPTCHA and account recovery can require the person.
Saved credentials must not be presented as a way to bypass those requirements.

## Implementation inside the existing services

**AX** owns account selection, secure input, consent/handoff and connection state.
**Core** owns account policy, Microsoft OAuth, tool dispatch and credential
references. **Computer** observes and acts on permitted pages, resolving a
credential only at the trusted execution boundary. **Temporal** carries opaque
references and action metadata, never plaintext passwords or token values.

Add bounded `browser_observe`, `browser_click`, ordinary-text `browser_fill`,
key and scroll tools. Bind element references to the session, current page
observation and control epoch. Preserve human takeover and do not replay
uncertain submissions. Secret entry uses a separate account-reference action.
Sanitize authentication redirects before returning URLs to the model or logs.

Both Core and Computer currently allow only the exact Git High hostname.
Authenticated sites need an explicit, tested redirect/resource policy and a
public-network destination boundary. This change does not broaden that policy.

## Durable storage and Outlook

### Reuse available in Workmates

Workmates API source `15b268a0502bc983a8ad835d8707e7d7148163e9` already
implements Google Drive OAuth in
[drive.py](https://github.com/Alambdasystem/workmates-api/blob/15b268a0502bc983a8ad835d8707e7d7148163e9/drive.py):
one-time state tied to the initiating admin session, PKCE, account labels,
encrypted refresh-token persistence, server-side refresh, and scoped mounts.
This is a reusable connection pattern. It is not a Microsoft implementation or
a generic website-password vault.

Its SQLite path comes from `DB_PATH`, and its Fernet key is created beside that
database. Adapt persistence and key management to REIGN's own boundary rather
than sharing Workmates records or copying that storage layout unchanged. Use
Microsoft's account/cache lifecycle for Outlook. The inspected Google tests
substitute provider HTTP responses; this audit does not establish a live OAuth
connection. Workmates services and data remain untouched.

### Required durable storage

A reusable login must survive a new browser context and service restart. Use an
existing dedicated vault where available, or an encrypted durable REIGN store
with its encryption key managed separately. An in-memory dictionary or ephemeral
container file does not satisfy this requirement. The existing Temporal disk
must not be repurposed. Restricted database setup remains subject to
[the documented isolation and administrator requirements](REIGN_DATABASE_SETUP.md).

One optional integration is a restricted 1Password service account using its
Python SDK. It can resolve references and manage items without adding a hosted
daemon, but an accessible dedicated vault and suitable permissions must first
be configured. This is a design option, not an assumed existing account.

For Outlook operations, use delegated Microsoft Graph authorization-code flow
with PKCE through AX/Core and an exact registered HTTPS callback. Keep an
encrypted MSAL token cache per account and request mail permissions incrementally.
Reading mail, modifying it and sending it are separate capabilities. A Graph
connection does not itself sign the managed browser into Outlook's website;
visual website work needs its own browser session. Reconnection remains
necessary when consent or tokens expire or are revoked.

## Acceptance for the follow-on feature

- Real typed and spoken requests invoke the same appropriate tools.
- Page observation and ordinary form actions work with takeover fencing intact.
- Save a test account, start a new context, restart the service, and reuse it.
- Wrong-origin and wrong-owner credential use fails; secret values stay out of
  model messages, Temporal history, traces, URLs, screenshots and repository files.
- Outlook reaches the selected authenticated account with only granted scopes;
  required human verification pauses and resumes honestly.
- Account/repository creation is confirmed by the external service, and failures
  or uncertain outcomes never trigger duplicate submissions automatically.

## Primary design references

- [Microsoft authorization code flow and PKCE](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
- [MSAL Python per-account token-cache persistence](https://learn.microsoft.com/en-us/entra/msal/python/advanced/msal-python-token-cache-serialization)
- [Microsoft Graph permissions](https://learn.microsoft.com/en-us/graph/permissions-reference)
- [Microsoft password-grant limitations](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth-ropc)
- [1Password SDKs and service-account authentication](https://www.1password.dev/sdks)
- [1Password service accounts](https://www.1password.dev/service-accounts)
- [Playwright authentication-state handling](https://playwright.dev/python/docs/auth)
