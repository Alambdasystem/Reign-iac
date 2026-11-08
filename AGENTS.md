# REIGN repository guidance

- Keep the infrastructure milestone separate from the browser/human-takeover E2E milestone.
- Render Blueprint is the single owner of the four REIGN pilot resources.
  James explicitly approved this ownership change, the $64.25/month base pilot,
  and initial launch without REIGN database persistence on 2026-11-08.
  Reuse Blueprint `exs-dbofqkm417fc7384kou0` in Jim's workspace; do not create a
  duplicate. The legacy OpenTofu plan/apply workflow is disabled. Re-enabling it
  requires a reviewed ownership/state migration. Keep Blueprint auto-sync off
  and promote only reviewed, tested image digests. Continue fixes and verification
  within the existing approved scope without repeatedly requesting approval.
  Preserve Workmates, Git High, Moby Alpha and the shared database. Scope/cost
  increases or destructive changes require their own concrete authorization.
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
