# Avatar Lab member access release — 2026-11-21

## Source and release evidence

[PR #96](https://github.com/Alambdasystem/Reign-iac/pull/96) adds named Avatar Lab
accounts, owner-only API enforcement, revocable sessions and visible logout.
[PR #97](https://github.com/Alambdasystem/Reign-iac/pull/97) keeps the workspace
logout button clear of the fixed Connections menu and adds actual browser
click-target checks at phone and desktop widths.

The selected application source is
`93107ddc169a450f581f4c8ac6d78a4d9f97f73d`.
[Correction PR validation](https://github.com/Alambdasystem/Reign-iac/actions/runs/44877138377)
and [main validation](https://github.com/Alambdasystem/Reign-iac/actions/runs/44877684282)
passed all four required infrastructure, Python, PostgreSQL and container checks.

The [gated release](https://github.com/Alambdasystem/Reign-iac/actions/runs/44878103291)
tested its exact images before publishing and succeeded on that source. Its
[artifact](https://github.com/Alambdasystem/Reign-iac/actions/runs/44878103291/artifacts/14696968069)
is `tested-release-93107ddc169a450f581f4c8ac6d78a4d9f97f73d`, 4,101,130 bytes.
The downloaded ZIP matches GitHub's SHA-256:

`d198a957fcaf57af5b63ace80c29f0ebc1e271d46ad4e0a537e024107342be3a`

The release manifest, provenance, four-service smoke and controlled workspace
receipts identify the same source. This record establishes tested image
selection; live rollout status is recorded separately by Render's deployment
history.

## Acceptance demonstrated by the selected release

- A synthetic named account signs in through real Chromium and lands directly
  in Avatar Lab without starting owner workspace recovery.
- The account can read its identity and use the lab, while browser, saved
  accounts, chat/agent, voice, security, Genesis, nodes and Open STL management
  requests are rejected.
- Real pretrained neural inference produces four nonblank frames, with mouth,
  pose and smile controls changing actual output pixels. No portrait is uploaded.
- Visible logout clears the cookies, and replaying the copied cookie fails.
  Incorrect CSRF and foreign-origin logout requests fail. A separate owner
  session survives the tester's logout.
- The main workspace logout button is in the viewport and receives pointer
  input at widths of 360, 390, 430 and 1280 pixels. Playwright trial clicks pass
  both before and after the existing service-restart acceptance phase.

These automated results use disposable credentials and a bundled fictional
portrait. They do not claim that a human has tested this new login on a physical
device. The user previously confirmed that the existing portrait prototype works
on their device.

## Selected promotion

Reuse Blueprint `exs-dbofqkm417fc7384kou0` with `autoSync=false`. Promote only
the existing AX service to:

`ghcr.io/alambdasystem/reign-ax@sha256:45610e5370051cb633208cee9f24bef676d5698a60bb52d9a026a1d6744927d9`

Core, Computer and Temporal retain their existing image pins. All compute plans,
instance counts, disks, pre-deploy commands and other resources are preserved.
The named account's salted verifier is provisioned privately through the existing
AX service's `AX_AVATAR_USERS_JSON` environment variable, using merge semantics.
The Blueprint contains only its `sync: false` placeholder. No real password or
verifier is included in Git, images or these receipts.

Existing owner cookies require one fresh sign-in when the new session format is
deployed; the owner password is unchanged. Sessions remain local to the current
single AX process. See the [access contract](../APPLICATION_CONTRACTS.md#owner-and-avatar-lab-access)
for account provisioning, revocation and future scaling requirements.
