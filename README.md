# REIGN Harness / Kaleidoscope

Infrastructure first. This repository is the **temporary** source for REIGN;
[Git High](https://alambda-forge.onrender.com/) is the intended long-term authority.

**Current stage:** the four-service managed-browser pilot is live on
Render through Blueprint after successful CI and James's approval. See the current
[deployment receipt](docs/DEPLOYMENT_RECEIPT.md) for service/deploy IDs and actual
verification results. Live human acceptance is separate and remains outstanding.

## Two milestones

1. **Infrastructure as code:** versioned Render Blueprint configuration, independent Docker
   services, generated runtime secrets, CI validation, reviewed plans, guarded apply,
   and per-service deployment verification.
2. **End-to-end behavior:** UltraView AX submits a task to Core; Temporal dispatches
   it to Computer; Playwright opens Git High; AX shows live frames; James takes
   control, interacts, and returns control. Preserve screenshots, workflow IDs,
   build digests and actual results. A public Git High page opening alone is not
   this milestone.

## Topology

| Component | Render service | Exposure | Responsibility |
|---|---|---|---|
| UltraView AX | `reign-ax-pilot` | HTTPS public; authenticated sessions | UI, live view, human takeover, server-side Core proxy |
| REIGN Core | `reign-core-pilot` | Private HTTP :10000 | Task/session API and Temporal client |
| Temporal | `reign-temporal-pilot` | Private gRPC :7233 | Durable pilot workflows; 1 GB disk |
| REIGN Computer | `reign-computer-pilot` | Private HTTP :10000 | Playwright browser and Temporal worker |

All are independent services in Oregon in Jim's workspace. Private networking
is scoped to the Render workspace and region; it is not a separate tenant VPC.
The Temporal development server is a **single-node pilot**, not a production
Temporal cluster. It uses persistent SQLite; production needs a separately
reviewed database/cluster and backup design.

Image-based deployments keep the runtime independent of the Git host. Docker
builds produce immutable SHA256 digests, which are reviewed in
[`deploy/release.json`](deploy/release.json). Render Blueprint is the sole owner of the pilot
resources. Its pinned digests are in `deploy/render-pilot.proposed.yaml` (the
original proposal filename is retained). The legacy OpenTofu workflow is disabled;
its release manifest remains `provision:false` and must not manage these services.

Workmates and the existing Git High service are outside this configuration.
The plan policy protects their known IDs; nothing is imported or renamed.

## Run validation

Requires OpenTofu 1.13.0, Python 3, Docker for container checks:

```sh
tofu -chdir=infra/render fmt -check -recursive
tofu -chdir=infra/render init -backend=false -lockfile=readonly
tofu -chdir=infra/render validate
tofu -chdir=infra/render test
python3 -m unittest discover -s tests -v
docker build --platform linux/amd64 -f services/core/Dockerfile -t reign-core:ci .
docker build --platform linux/amd64 -f services/ax/Dockerfile -t reign-ax:ci .
docker build --platform linux/amd64 -f services/temporal/Dockerfile -t reign-temporal:ci .
docker build --platform linux/amd64 -f services/computer/Dockerfile -t reign-computer:ci .
bash scripts/container_smoke.sh
```

The OpenTofu tests mock the provider and never provision real services. The
container smoke test now runs all four services with the real AX UI, Temporal
workflow history, fresh frames, takeover/handback, stale-command rejection and
restart recovery. It does not establish deployed or human acceptance.

Read [deployment setup](docs/DEPLOYMENT.md),
[application contracts](docs/APPLICATION_CONTRACTS.md), and
[the next-session handoff](docs/HANDOFF.md) before changing the deployment.
