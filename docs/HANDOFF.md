# Continue REIGN from another ChatGPT session

Temporary repository: https://github.com/Alambdasystem/Reign-iac
Long-term destination: https://alambda-forge.onrender.com/

GitHub write access through the Work session's connector was verified with the
initial commit `dc88c05d29d9eec6fa9798e928c21e66ec9d99e6`. That does not prove a
different ChatGPT mode/session has write tools or the same account permissions.
Official product guidance says tools depend on plan/platform/workspace settings:
https://learn.chatgpt.com/docs/use-chatgpt

First ask the new session to read this repository and say which GitHub tools it
actually has. If it can write, make an isolated documentation branch/PR and read
back the commit SHA. If it can only read or draft, have it produce patches and use
a write-capable session or GitHub to apply them. Do not hand it passwords/tokens
in prompts and do not assume switching modes guarantees a particular credit cost.

## Copy this into the next session

> Continue Alambdasystem/Reign-iac. Read README.md, docs/DEPLOYMENT.md,
> docs/APPLICATION_CONTRACTS.md and the latest CI results first. Confirm your actual
> repository read/write capability. Keep infrastructure ownership in OpenTofu;
> GitHub is temporary and Git High is the intended long-term source of truth.
> Implement REIGN Core and UltraView AX as independent Docker services, complete
> and test the experimental Computer worker, and use the existing Temporal pilot
> container. Implement authenticated live screen viewing and human takeover with
> stale-command rejection. Build/publish immutable images through the pipeline,
> update deploy/release.json only with tested digests, and prepare a concrete
> costed plan before seeking approval for paid changes. Preserve Workmates and
> Git High; never commit secrets. Keep Tailscale/GPU workers deferred. Do not claim
> deployment or E2E success without actual evidence. Run the documented browser
> acceptance flow opening Git High, including James's real takeover and handback.

## Still outstanding

- Core/AX application implementations and Dockerfiles; full Computer tests.
- GitHub protected environments/branch policy, CI secrets, state server and lock
  verification; these require account configuration not accomplished by YAML.
- Four released container digests; Render private-registry credentials if needed.
- Current price review, explicit paid approval, actual OpenTofu plan/apply.
- Live per-service verification, browser E2E and human acceptance.
- Authenticated Git High source migration and later Tailscale integration.

Consult live CI for validation results on the current commit. Infrastructure
validation, mock provider tests and container smoke are separate from deployment.
