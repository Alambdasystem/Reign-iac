# Core Tailscale promotion receipt

## Prepared checkpoint — 2026-11-22

The requested Core-only promotion is prepared in the existing Render Blueprint.
**Manual Blueprint apply, device enrollment, backend SSH and live application
verification are pending at this checkpoint.** No deployment or enrollment is
established by the release evidence below. The one-off enrollment key has been
configured privately on the existing Core service; its value is not part of
this promotion.

The authorized connection is outbound SSH on TCP 22 to three designated Windows
backend devices. The source identity uses `tag:reign-render-core`. Private
device addresses, account identities and credentials stay outside this receipt.
Tailscale does not replace the backend's SSH server, OS login authorization or
host-key verification.

## Exact promoted configuration

| Setting | Prepared value |
| --- | --- |
| Service | Existing Core private service |
| Core image | `ghcr.io/alambdasystem/reign-core@sha256:c5471a63e16902ee261df0bddcdf308a6399bef1dd996cf6b9e00a08bbe85bfc` |
| `REIGN_TAILSCALE_ENABLED` | `1` |
| `REIGN_TAILSCALE_HOSTNAME` | `render-reign-core` |
| `REIGN_TAILSCALE_TAGS` | `tag:reign-render-core` |
| Enrollment credential | Existing `REIGN_TAILSCALE_AUTHKEY` private `sync: false` setting |
| Persistent state | Existing Core disk, `/var/data/reign-vault/tailscale` |
| Deployment owner | Existing manual Render Blueprint; auto-sync remains off |

Only the Core image pin, enable flag and enrollment tag change in the Blueprint.
The hostname is retained. The other three image pins, plans, instance counts,
disks, service references, existing environment settings, pre-deploy acceptance
and `dockerCommand` values are retained.

Core remains a nonroot userspace client. No advertised subnet route, exit node,
inbound Tailscale SSH server or public proxy listener is enabled by this change.
Core's existing disk means the rollout interrupts Core while the disk moves.

## Release evidence

| Receipt | Verified value |
| --- | --- |
| Implementation PR | [#109](https://github.com/Alambdasystem/Reign-iac/pull/109) |
| Source commit | `6364698dbcf88b4442f9d3342650bd9d6836c73b` |
| Main validation | [44975282731 — successful](https://github.com/Alambdasystem/Reign-iac/actions/runs/44975282731) |
| Gated image release | [44975927208 — successful](https://github.com/Alambdasystem/Reign-iac/actions/runs/44975927208) |
| Release completed | `2026-11-22T02:31:15Z` |
| Artifact | [14735208242](https://github.com/Alambdasystem/Reign-iac/actions/runs/44975927208/artifacts/14735208242) |
| Artifact name | `tested-release-6364698dbcf88b4442f9d3342650bd9d6836c73b` |
| Artifact ZIP SHA256 | `fa96e3b2a012959c2466a8d7121f35b893ecc2d0f31d33575028fe39e5d6bfe2` |

The downloaded artifact's ZIP hash matched GitHub metadata. Its
`release/release.json` contains the Core digest above, all four valid image
digests and `provision: false`. Its `release/provenance.json` identifies the
source commit above and agrees with the standalone smoke, initial workspace and
restart workspace receipts. All passed, including saved-login persistence after
restart. This release receipt does not enable the retired OpenTofu owner.

Main validation passed all four required checks: infrastructure, Python tests,
PostgreSQL integration and containers. Application CI passed the real offline
Tailscale daemon smoke with v1.104.7 as UID 10001, networking disabled and no
enrollment. The gated release rebuilt the images, tested those exact images
with four-service acceptance, then published the same images and recorded their
digests. It did not repeat the separate offline Tailscale smoke against the
final CD-built digest.

Controlled workspace acceptance uses a scripted model and controlled website.
It does not establish live tailnet enrollment, backend SSH, live provider media
or human acceptance.

## Pending live receipt

Record the reviewed Core-only Blueprint plan and terminal deployment result
after the private enrollment credential is ready. Then verify the intended
service identity, a read-only SSH command to an available approved backend,
authenticated application readiness, and a restart that retains the same
Tailscale identity after removing the one-off enrollment key.

The prior Core image is
`ghcr.io/alambdasystem/reign-core@sha256:9ea055bea221c946c613535ce7f5630c5d14ea35cf4bd40ebd0148eb0078bc60`.
Rollback uses the same manual Blueprint: disable the client and, if needed,
restore that prior Core digest while preserving the existing disk and secrets.
