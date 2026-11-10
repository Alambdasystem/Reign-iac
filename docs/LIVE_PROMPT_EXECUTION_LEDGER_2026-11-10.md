# Live AX prompt-to-tool acceptance evidence ledger — 2026-11-10

**Target:** https://reign-ax-pilot-d934.onrender.com, authenticated Work interface.
**Status:** 0/21 verified live; **all NOT_RUN**, not FAIL, because this GitHub/Render connector session does not expose authenticated UI click/typing/screenshot control. Neither fixture/container smoke tests nor Render 200 responses count as execution.
**Baseline:** use exact deployed Core, AX, Computer and Temporal digests at execution time; do not assume current branch corresponds to promoted images.
**Prompts:** see docs/LIVE_PROMPT_ACCEPTANCE_MATRIX.md; copy exact prompt there when writing each receipt.
**Security:** no credentials, raw prompts, cookies or secrets in logs; disposable nonsecret fixtures only. Real human takeover and restart cases need coordinated maintenance window.

| ID | Expected tool/gate | State | Live trace/screenshot evidence |
|---|---|---|---|
| L01 | `workspace_status` | NOT_RUN | unavailable |
| L02 | `browser_status` | NOT_RUN | unavailable |
| L03 | `open_browser` | NOT_RUN | unavailable |
| L04 | `navigate_browser` | NOT_RUN | unavailable |
| L05 | `browser_observe` | NOT_RUN | unavailable |
| L06 | `browser_click + browser_observe` | NOT_RUN | unavailable |
| L07 | `browser_fill` | NOT_RUN | unavailable |
| L08 | `browser_press + browser_observe` | NOT_RUN | unavailable |
| L09 | `browser_scroll + browser_observe` | NOT_RUN | unavailable |
| L10 | `vault_status` | NOT_RUN | unavailable |
| L11 | `vault_list_accounts` | NOT_RUN | unavailable |
| L12 | `vault_request_account` | NOT_RUN | unavailable |
| L13 | `vault_prepare_signup` | NOT_RUN | unavailable |
| L14 | `vault_use_account` | NOT_RUN | unavailable |
| L15 | `takeover fence` | NOT_RUN | unavailable |
| L16 | `guarded graph resume` | NOT_RUN | unavailable |
| L17 | `stale-reference rejection` | NOT_RUN | unavailable |
| L18 | `unsafe destination rejection` | NOT_RUN | unavailable |
| L19 | `vault origin rejection` | NOT_RUN | unavailable |
| L20 | `restart checkpoint / no replay` | NOT_RUN | unavailable |
| L21 | `owner-scoped trace` | NOT_RUN | unavailable |

## Execution instructions
1. Authenticate through the existing AX UI using an approved session. Do not paste credentials into CI or GitHub.
2. For each case, submit the exact prompt through Work UI and capture the actual selected MCP tool, Core run ID, Temporal workflow ID (if any), screenshot/observation ID and visible effect.
3. Record timestamp, exact deployed image digests, PASS/FAIL/BLOCKED/NOT_RUN and safe error code in a separate receipt. Do not infer success from HTTP status, synthetic tests or tool capability lists.
4. For L15–L20, verify takeover, stale-ref fencing, wrong-origin rejection and restart/non-replay using only disposable resources and an explicitly reviewed restart window.
5. Critical cases L01–L06, L10–L12, L14–L20 must pass before beta signoff; unobserved must remain NOT_RUN.

**No tests were typed into the authenticated production AX UI in this connector session.**
