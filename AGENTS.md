# REIGN repository guidance

- Keep the infrastructure milestone separate from the browser/human-takeover E2E milestone.
- Use OpenTofu as the single owner of new Render resources. Do not adopt existing
  Workmates or Git High services, change billing, or perform destructive changes
  without explicit approval for the concrete plan.
- Never commit credentials, state, raw plans, recordings containing credentials,
  or authenticated browser profiles. This repository is public.
- Use independent Docker services for Core, Temporal, Computer and UltraView AX.
  Update pinned image digests through reviewed infrastructure changes.
- GitHub is temporary. Git High is the intended long-term source; do not claim
  migration until authenticated push and ref parity are verified.
- Run `.github/workflows/validate.yml` checks for relevant changes. Report actual
  results; mocked tests and container smoke are not live deployment or human E2E.
- Tailscale and Windows GPU/LLM workers remain deferred. Do not enroll devices,
  expose remote shells, or assume network access from saved hostnames.
- Read `docs/HANDOFF.md` and `docs/APPLICATION_CONTRACTS.md` before application work.
