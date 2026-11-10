# REIGN durable history, observability and beta acceptance — implementation contract

**Status: proposal, NOT production-enabled.** This branch adds the schema foundation only. No migration was executed, and it does not change Render, database roles, credentials, LangGraph runtime, AX, or the deployed experience.

## Product outcomes
- **History:** Per-owner search, pagination, reopen, archive/delete, and resumable conversations, with explicit distinctions between chat text, LangGraph checkpoint state, Temporal workflow state and browser cookies.
- **Observability:** One trace linking AX prompt submission → Core LangGraph nodes → MCP tool name → Temporal workflow/activity → Computer browser action → result. Include timestamps, durations, input/output token counts, sanitized error codes, image/commit identity, and user-facing PASS/FAIL evidence.
- **Admin view:** Tool-selection accuracy, tool success, failure patterns, latency percentiles, token/cost estimates, beta cohort metrics, and a redacted event timeline with per-owner permissions.
- **Beta readiness:** Demonstrate one typed prompt each for status, open, navigate, observe, click, ordinary fill, press, scroll, vault status/list/use, pause/takeover, handback/resume, cleanup, stale inputs and blocked destinations. Record exact prompt, selected tool, Temporal workflow ID, observation and screenshot or log reference. Never infer a tool pass from HTTP 200 alone.

## Data and security design
Migration `003_reign_history_telemetry.sql` proposes `reign.conversations`, `reign.conversation_messages`, `reign.graph_checkpoints`, and `reign.trace_spans`. Tables are restricted to the REIGN schema with RLS enabled and forced and **no runtime grants/policies yet**. Application code must not write until separate reviewed grants, owner policies, transactions, tests and key management have landed. Do not use the Moby Alpha application role.

- Encrypt message bodies and checkpoint state with authenticated, versioned envelope encryption in Core **before persistence**; never derive this key from the separate website vault key. Implement an authenticated and size-limited serializer; treat stored serialized content as untrusted.
- Record **only allowlisted span attributes**, not raw prompts, hidden chain-of-thought, provider request/response bodies, screenshots, passwords, tokens, cookies, vault grants, authorization headers, full URLs with query strings, or browser form field values.
- Scope **every read and write to the authenticated owner**. Propagate actor and trace context through AX/Core/MCP/Temporal/Computer without trusting model-provided IDs. Set owner context transaction-locally in the database; test cross-owner reads, grants, and RLS. Separate admin aggregates from access to private content.
- Design retention and deletion (e.g. configurable history retention, shorter raw diagnostic retention), export, redaction, legal hold, per-owner delete, key rotation, backup/restore and migrations before public beta.
- Model a restarted/expired browser explicitly: conversation continuation must **not** replay a previous click, password fill or external submission. Resume only with fresh observation and ownership/epoch checks.
- Prefer OpenTelemetry/W3C trace context for cross-service correlation; LangSmith, if later enabled, is an optional exporter with explicit data/privacy review. Keep OpenAI Responses `store=false` as currently configured.

## Staged implementation — each stage requires tests and review
1. **Database readiness:** restricted REIGN role, reviewed grants/RLS policies, migration test with two owners and explicit negative permissions against other app schemas; no new paid database.
2. **Core persistence:** encrypt/decrypt messages, persist checkpoints, idempotent writes and restart recovery; integration tests for loss, corruption, key rotation, and cancellation without replay.
3. **Trace collection:** inject trace IDs through authenticated request, LangGraph model/tool events, MCP adapter and Temporal workflow/activity; scrub metadata before save; expose owner-scoped paginated trace API and admin aggregates; prove tokens, timing and error codes align across services.
4. **UI:** add History and admin-only Observability to AX. Search with access control, conversation/trace timeline, resume with explicit stale-browser warnings, screenshot references only after redaction.
5. **Beta acceptance:** run real typed prompts in deployed signed-in AX, record evidence for each tool and negative security case, recover across actual Core restart, inspect privacy leakage, and obtain human signoff before advertising history/telemetry as released.

## Non-goals / release controls
No new Render service, paid product, account creation, social posting, database migration execution, or automatic Blueprint sync. Existing Blueprint is the sole infrastructure owner. Merge only after CI and an independent review; deploy only reviewed immutable images and the required approval gates. Keep PR #20's browser/session fixes independently reviewable.

## Marketing evidence and screenshot brief
Capture **real, redacted** screenshots from deployed REIGN: (1) UltraView Work interface, (2) controlled Git High browser action, (3) Accounts panel with **fake test metadata**, and (4) trace/history dashboard **only after deployed and verified**. Crop secret-bearing areas, redact test account identifiers, avoid screenshots showing real tokens or private sites. Label any concept art `concept`, never a live product screenshot.

The beta invitation must accurately say that interactive browser/MCP and encrypted saved accounts are deployed for limited pilot testing, while persistent History and Observability are in development. Invite testers to report tool errors and focus on feedback; do not imply fully general-purpose internet access (current allowlist is Git High only).
