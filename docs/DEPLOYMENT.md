# Deployment setup and approval

## What is configured in code

- `validate.yml`: push/PR checks without Render credentials; mocked OpenTofu tests
  plus the reusable `reign-app.yml` application workflow. It runs Python regressions,
  real PostgreSQL 18 migration/isolation tests in a disposable database, and a
  four-container AX/Core/Temporal/Computer browser acceptance test.
- `reign-cd.yml`: after successful validation of the current main push, rebuilds
  and tests the four exact images, publishes them, and records their registry
  digests in a disabled release candidate artifact. No deploy hooks or Render
  mutations occur here. Promote reviewed digests through OpenTofu only.
- `build-image.yml`: manual per-component build. Publishing to GHCR is opt-in and
  does not deploy to Render. Core/AX Dockerfiles are implemented. Inspect package visibility and Render pull access before use.
- `infrastructure.yml`: manual plan, sanitized review summary, encrypted plan
  artifact, a separately gated apply of that exact plan, live verification.

These files alone do not configure GitHub environments, secrets or a state
server. The apply job is disabled unless `REIGN_APPLY_ENABLED` is explicitly set
to `true`. Do not enable it before the following setup is verified.

## Required setup (not yet completed)

1. Implement and test Core and AX per the contracts; complete Computer acceptance
   and E2E tests. Build all four linux/amd64 images. Replace the disabled release
   manifest with the four audited image repositories/digests and `provision:true`.
   Render private-registry credential IDs can be supplied per image; passwords
   cannot be placed in the manifest.
2. Select an existing HTTPS state backend that implements OpenTofu HTTP state
   **and locking**, with encrypted storage, restricted access and backups. A Git
   repository/file URL is not a state backend. No state service was provisioned.
   Validate competing-client lock rejection and restore before the first apply.
3. Configure `reign-plan`, `reign-release`, and `reign-apply` GitHub environments.
   Restrict them to `main`. Require human review for `reign-apply` and prevent
   self-review/bypass where the account permits. Environment creation/protection
   has not been performed or verified. Protect `main` with validation checks.
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
   1 GB persistent disk. No budget approval or live pricing quote is recorded.
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

Application changes build a new image; review its digest in the release manifest,
then repeat plan/approve/apply. Roll back by reviewing and applying a prior known
image digest. Check Temporal database compatibility first; never blindly roll
back its schema. Do not set `provision=false` after services exist: that requests
deletion and is blocked by `prevent_destroy` and the plan policy.

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
