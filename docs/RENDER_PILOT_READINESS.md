# REIGN Render pilot readiness — 2026-11-08

Target: Jim's workspace (`tea-dbdccfpsrm7s73b8rugg`), Oregon.
Keep Git High and Workmates unchanged. Render first, Tailscale second.

## Verified
- CI on `f65d9c8`: infrastructure and application workflows succeeded; all four Docker builds and container smoke passed.
- Moby Alpha PostgreSQL: `dpg-dbk48oqjnfac739i4nl0-a`, Oregon, PostgreSQL 18, 1 GB allocated, 256 MB RAM.
- Sampled database memory approximately 71 MB; CPU low; connections usually zero.
- External SQL inspection blocked by existing empty IP allowlist. Do not open access just for inspection.

## Database decision
Do **not** treat sampled memory or low CPU as proof of spare disk capacity.
Before reuse, inspect database size and connection limits through an authorized
internal connection, and confirm Moby Alpha's ownership and backup/restore.
Prefer a separate database and dedicated least-privilege role on the same
Postgres instance. A separate schema is fallback if separate DB creation isn't
permitted. Keep REIGN migrations isolated; never run them against Moby tables.
The current Core/AX/Computer code does not yet have a verified PostgreSQL
persistence integration, so database reuse is not a prerequisite for the first
stateless browser pilot.

## Deployment gates
1. Finish real adapter and Core/AX/Temporal routing tests, including authenticated
   browser viewing, takeover, handback, stale epoch rejection and cleanup.
2. Publish tested linux/amd64 images to a registry Render can pull; record exact
   immutable digests in `deploy/release.json` only after verification.
3. Set up protected GitHub environments, runtime secrets, and a locked encrypted
   OpenTofu state backend. Verify lock contention and backup/restore.
4. Run a costed OpenTofu plan for the four services. Record exact instance
   sizes and disk costs before enabling the apply gate.
5. Apply the reviewed plan, verify live health and image digests, and run Git High
   E2E acceptance with real human takeover. Preserve evidence and rollback path.
6. Only after successful Render acceptance, design scoped Tailscale connectivity
   and migrate workers one at a time with fallback.

No paid services have been created by this document. No database credentials,
permissions, schemas, or networking were changed.
