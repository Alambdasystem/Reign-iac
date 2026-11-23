# REIGN connection registry and vault migration

[Issue #137](https://github.com/Alambdasystem/Reign-iac/issues/137) adds personal
provider connections to the private console and Workspace. The first change
exposes a curated catalog to signed-in workspace users. The API reports both
providers as unavailable and publishes an empty tool list until an adapter has
passed authorization, discovery and verification checks.

## Identity and storage

- AX validates the signed-in session and forwards a short-lived, audience-bound
  actor assertion to private Core. Core resolves active membership and explicit
  integration or vault grants and derives the tenant and stable user ID.
  Workspace `rain` is the current team namespace. Its membership and the
  legacy `reign-owner` identity are existing sources to verify during review.
  A connection, tool argument, room ID or OAuth redirect cannot select a subject.
- Keep the OAuth registry separate from website credentials. In the registry,
  connections are keyed by tenant, user and provider. Store provider issuer and
  stable subject, verified display identity and timestamp, granted scopes,
  key ID and encrypted token bundle, plus a revision. AES-GCM associated data
  covers tenant, user, provider and revision. Store consent attempts with a
  hashed one-time state, expiry, session binding, expected issuer and encrypted
  PKCE verifier. Audit with action, actor, provider, revision and result, leaving
  out secrets and provider response bodies.
- Keep the dedicated vault mount and fail-closed key checks. Revise the
  account and grant schema to carry both tenant and user. Tenant and user
  participate in all lookups, label collision checks and encrypted record
  bindings. Bind grants to the same subject plus account revision, origin,
  browser session, observation and control epoch. Core authorizes the
  workspace and browser scope before issuing or redeeming a grant. A worker
  obtains the secret only after one-time redemption under the existing
  private worker protocol. Use a short-lived, action-specific reauthentication
  receipt for any explicit reveal to the human in AX.

## Provider authorization and MCP

- Register the two providers with fixed HTTPS endpoints and callbacks.
  OAuth starts with an authenticated same-origin, CSRF-protected request
  that records explicit consent. Use S256 PKCE, single-use state and an OIDC
  nonce where an ID token is used. AX currently sets its session cookie
  `SameSite=Strict`. Stage the callback in a bounded, short-lived receipt
  and complete the exchange only through a subsequent same-origin,
  CSRF-protected request with the initiating AX session. This keeps the
  main session cookie strict and binds exchange to the original user.
  Keep callback queries out of logs and use a no-referrer clean return URL.
- Validate the expected issuer, audience, signature, expiry and nonce for
  OIDC tokens. Perform a server-side profile fetch and verify the stable
  subject before marking that provider connected. Reconnect obtains fresh
  consent. Use a compare-and-swap revision when replacing or refreshing
  grants. Disconnect blocks new tool calls and invalidates pending consent
  locally before contacting the provider. Track external revocation separately
  so the UI can report where provider-side consent still needs removal.
- Keep OAuth exchanges in trusted application code and extend Core's fixed
  MCP boundary with approved servers or adapters. Validate tool discovery
  against pinned names and input/output schemas. Expose only tools covered
  by active per-user grants and verify scope and connection revision again
  at execution. Bind impactful actions to action-specific human consent.
  Use provider tokens only at their intended upstream API. An MCP resource
  that requires authorization receives a separate audience-bound token.
  Raw tokens, provider errors and passwords stay out of model output and traces.

## Migration and release sequence

1. Review the mapping from existing owner records to a verified tenant and
   stable owner ID. Inventory record counts without exporting plaintext.
   Verify backup and restore on a disposable mounted copy.
2. Quiesce writes and grant issuance on the dedicated mount. In a single
   transaction, verify legacy bindings, re-encrypt each record with the
   new subject binding, rebuild label indexes and invalidate legacy grants.
   Verify counts and post-migration decryptability before committing.
   Rollback prior to accepting new writes uses the verified snapshot.
   After new writes are accepted, a rollback requires a reviewed export or
   forward fix to avoid losing new records.
3. Test cross-user/tenant access on every endpoint, including worker grant
   redemption, reauth replay, key and disk failures, rollback and restart
   durability. Test both provider identities independently, stale or revoked
   grants, refresh races, callback replay and mismatched provider metadata.
4. Enable access per explicit grant after review. Exercise the picker and
   provider flows on mobile and desktop. Use the existing reviewed PR,
   CI, immutable build and Render digest/health gates for promotion.
