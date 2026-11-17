# REIGN Meetings

Isolated LangGraph assistant for Open STL multiplayer meetings. This is a new
deployment, separate from the four administrator pilot services. It neither
imports nor connects to Core, Computer, Temporal, the account vault or histories.
No pilot resource or shared database is modified. All model calls are server-side.

Build: `pip install -r meeting_service/requirements.txt && python -m unittest meeting_service.test_app`

Start: `uvicorn meeting_service.app:app --host 0.0.0.0 --port $PORT --no-access-log`

Environment: OPENAI_API_KEY from the existing authorized Workmates configuration;
MEETING_TOKEN, an independent service credential; MEETING_MODEL defaults to
gpt-6-luna. Only the Open STL server receives the meeting credential. No CORS
or browser session is provided. POST /reply accepts a bounded message and twelve
discussion lines; it returns the actual LangGraph answer. GET /healthz reports
configuration, not provider success. Missing key, unavailable model and exhausted
budget fail explicitly. Limits: 2 concurrent, 12 attempts/minute, 180/day/process,
600 output tokens, 25-second timeout, no retries. Counters reset on restart.
No request, transcript, recording or cross-meeting memory is stored by this
service. OpenAI receives the supplied discussion when participants invoke REIGN.

Tests exercise the real graph with a fake model, independent-meeting isolation,
authorization, body bounds and lack of administrator endpoints. Actual provider
and deployed game tests are required separately.

Open STL may authenticate with timestamped, nonce-protected HMAC-SHA256 requests. A dedicated signing key is derived from the already shared OpenAI configuration using domain `open-stl/reign-meetings/v1`; the provider credential is never transmitted between services. Signatures bind the method, path and exact body, expire after 60 seconds, and cannot replay within the same process. Rotating the shared provider credential must update both services. The separately generated bearer token remains a supported alternative. Run one instance; replay tracking and budgets are in process.
