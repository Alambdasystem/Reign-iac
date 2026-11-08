# Approved UltraView AX source

`UltraView_AX_v1.html` is James's approved 2026-11-07 design, recovered from the
original saved artifact. Preserve its ruby/blood-black fluid background, orb,
Living Canvas, responsive layout, navigation and expanded computer view.

The original is a visual prototype: voice, chat and the remote work timeline are
simulated. It is retained here as a design reference, not served as a live app.

The application in `services/ax/` reuses its CSS and fluid renderer. It replaces
the simulated workflow with authenticated AX → Core → Temporal → Computer actions,
fresh frames, takeover/handback, input, cleanup, reload recovery and fail-closed
connection handling. Workspace commands and LangGraph tools invoke those real
actions. Voice, external computer adapters and full desktop access remain
unavailable and are labeled honestly; they are not implemented by animated states.

API credentials remain server-side. The browser stores only non-secret session and conversation
identifiers in sessionStorage to reconnect after reload. No password/token is
stored there. Responsive and interaction acceptance is in `scripts/browser_smoke.py`.

`python -m reign.live_acceptance` is a one-shot public-AX API acceptance command
for the authorized Render rollout, using existing AX environment credentials
without logging them. A temporary startup invocation waits for the new frontend,
tests the actual live chain, and cleans only its own browser session. Remove that
invocation after collecting the result. `--ci-local` permits only the exact
disposable test origin `https://ax:10000` and its self-signed certificate; production
origin validation and normal TLS verification remain enabled otherwise.


The composer now runs a real LangGraph conversation through LangChain's OpenAI
Responses adapter. It displays real tool events, token usage, paused runs and
resume controls while computer takeover remains independently usable. The
live acceptance command additionally verifies actual OpenAI tool calls and
LangGraph interruption/resume outside CI; CI uses deterministic model doubles
only in graph unit tests and never supplies a production API key.
