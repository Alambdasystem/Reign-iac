# Deployment setup and approval

## Active Render Blueprint owner

James approved Blueprint ownership, the $64.25/month base four-service pilot and
initial launch without REIGN database persistence on 2026-11-08. PR #4 is merged.
Use existing Blueprint `exs-dbofqkm417fc7384kou0` in Jim's workspace
`tea-dbdccfpsrm7s73b8rugg`, branch main, file `deploy/render-pilot.proposed.yaml`,
with auto-sync disabled. The original proposal filename is retained to avoid
unnecessary path changes. See [DEPLOYMENT_RECEIPT.md](DEPLOYMENT_RECEIPT.md) for
actual deployed resources and verification results.

Render generates runtime credentials and shares only required service values.
No separate HTTP state backend or GitHub-to-Render API secret is required.
Never print generated secrets; human AX sign-in uses the password in the owner's
Render Environment settings. Preserve existing services and shared databases.

For updates: pass required CI, review immutable digests and the exact Blueprint
plan, then apply changes within the authorized scope and verify terminal deploy
status plus application behavior. Expanded scope/cost needs explicit approval;
routine fixes completing the approved deployment do not require repeated approval.
Use Render's documented `/bin/sh -c PROGRAM` format for custom commands, without
outer quotes around PROGRAM. Prefer the tested image CMD when it already supports
service host references. See the first startup failure in the deployment receipt.

## What is configured in code

- `validate.yml`: push/PR checks without Render credentials; mocked OpenTofu tests
  plus the reusable `reign-app.yml` application workflow. It runs Python regressions,
  real PostgreSQL 18 migration/isolation tests in a disposable database, and a
  four-container AX/Core/Temporal/Computer browser acceptance test.
- `reign-cd.yml`: after successful validation of the current main push, rebuilds
  and tests the four exact images, publishes them, and records their registry
  digests in a disabled release candidate artifact. No deploy hooks or Render
  mutations occur here. Promote reviewed digests through the Blueprint only.
- `build-image.yml`: manual per-component build. Publishing to GHCR is opt-in and
  does not deploy to Render. Core/AX Dockerfiles are implemented. Inspect package visibility and Render pull access before use.
- `infrastructure.yml`: disabled legacy OpenTofu workflow retained for migration
  reference. Both jobs are unconditionally disabled to avoid two deployment owners.

The active Blueprint does not use the legacy plan/apply secrets or state backend.
Do not enable the old workflow alongside it.

## Historical OpenTofu setup — inactive migration reference

The following checklist records the old deployment path, not current prerequisites.
Its pre-apply status statements describe that earlier checkpoint. Do not execute
this checklist unless an ownership/state migration is separately approved.

1. Four linux/amd64 images have passed automated browser acceptance and were
   published by release run 42527962724 from commit `bd01832f2b29c5587e763adbc6f17481fba74f64`.
   The release manifest now pins their digests, and remains `provision:false`.
   Anonymous registry manifest reads verified all four exact digests; the images
   are public. Verify actual Render pulls during deployment. Complete setup before proposing
   `provision:true` in a reviewed infrastructure change.
   Render private-registry credential IDs can be supplied per image; passwords
   cannot be placed in the manifest.
2. Select an existing HTTPS state backend that implements OpenTofu HTTP state
   **and locking**, with encrypted storage, restricted access and backups. A Git
   repository/file URL is not a state backend. No state service was provisioned.
   Validate competing-client lock rejection and restore before the first apply.
3. Configure `reign-plan`, `reign-release`, and `reign-apply` GitHub environments.
   These environments now exist with `main`-only rules. Apply requires review by
   `Alambdasystem`; administrator bypass is disabled. Self-review is permitted
   because only this owner is configured; add a designated second reviewer before
   enabling independent approval. Main branch protection is saved and verified:
   PRs, up-to-date branches, resolved conversations and all four GitHub Actions
   validation checks are required. Administrators cannot bypass these rules;
   force pushes and branch deletion are disabled. This pilot does not require
   an additional PR reviewer; the apply environment requires explicit review.
4. In both plan/apply environments set the same variables `TF_HTTP_ADDRESS`,
   `TF_HTTP_LOCK_ADDRESS`, `TF_HTTP_UNLOCK_ADDRESS`; the backend must support
   default POST update and LOCK/UNLOCK methods. Adapt explicitly for other servers.
5. Set secrets in the secret UI: `RENDER_API_KEY`, `TF_HTTP_USERNAME`,
   `TF_HTTP_PASSWORD`, `PLAN_ENCRYPTION_KEY` (32+ random characters), and
   `REIGN_RUNTIME_SECRETS_JSON` containing four distinct random values:
   `core_token`, `computer_token`, `ax_session_key`, `ax_admin_password` (32+ characters each).
   Use the same values in plan/apply environments. Set variable
   `REIGN_AX_PUBLIC_ORIGIN` to the exact AX HTTPS origin. Optional secret
   `REIGN_DATABASE_URL` must use a verified REIGN-only role and is passed only
   to Core; leave it unset for the stateless pilot. Never paste them into chat or
   Git, and never include real secrets in screenshots.
6. Run **plan**. Review the exact source commit, resource names, plans, image
   digests, disk and current pricing. Get James's approval for the concrete cost.
   Defaults request starter for AX/Core, standard for Temporal/Computer, and
   1 GB persistent disk. The 2026-11-08 published-price estimate is $64.25/month
   ($7 Core + $7 AX + $25 Computer + $25 Temporal + $0.25 disk), excluding usage,
   tax, workspace changes and any extra state/database service. Source:
   https://render.com/pricing. This is a budget estimate, not an actual plan or
   approved spend. No paid resources were created.
7. Only then set `REIGN_APPLY_ENABLED=true`, dispatch **apply** with the cost
   approval checkbox, and review the newly generated plan before approving the
   `reign-apply` environment. The job consumes the encrypted plan from that same
   run. State locking and stale-plan checks remain enabled. Deletes and
   replacements are rejected. There is no automated destroy route.
8. Verify all four live deployments and exact image digests. The verification
   script also requires AX health and authenticated dependency readiness. Then
   perform the distinct browser/live-view/human-takeover E2E acceptance run.

State and plan files contain runtime secrets even when variables are marked
`sensitive`. Only encrypted plans are uploaded, retained for one day; raw plan
and apply logs are withheld. The artifact hash checks corruption; access to
GitHub Actions artifacts and the encryption key must remain restricted.
The source repository is public. Deployment secrets must never enter Git.

## Updates and rollback

Build/test new images in CI, review their exact digests, update the Blueprint and
plan/apply only that reviewed change with auto-sync off. A rollback is another
reviewed deployment of a known compatible digest; it does not undo disk/database
changes. Check Temporal schema compatibility and plan for its disk-related restart.
Keep the retired OpenTofu manifest `provision:false`; it does not represent the
live Blueprint resources. Do not import them into another state without migration.

## Git High migration

No authenticated Git High repository or push/mirror has been verified yet.
Create a scoped repository/credential there when authorized, copy all branches
and tags without force/deleting existing refs, and compare hashes. Then designate
Git High as the sole write authority, make GitHub a one-way execution mirror if
needed, and test the pipeline from an authoritative commit. Gitea-compatible
Actions/runner behavior must be tested before moving execution. Move the release
manifest and encrypted/locked state configuration with the workflow; do not
create a second state to duplicate the same services.

## Later Tailscale / Windows GPU workers

No tailnet access or Windows SSH test has been performed in this setup. No
Tailscale key is stored. No devices or network routes are enrolled.

Future design: add a separately approved Render connector using Tailscale
userspace networking, ephemeral enrollment from secret storage, and ACL tags
restricted to the designated Windows worker and LLM port. Browser workers can
use a private explicit proxy; LLM calls should use a narrow authenticated gateway.
Do not expose SSH, LLM ports or a generic proxy publicly. Keep this optional and
disabled until the machine, SSH account, service and authorization are confirmed.
Windows GPU workloads can run separately from Linux browser containers; assess
WSL2/Docker support and GPU runtime on that actual machine before choosing a path.
