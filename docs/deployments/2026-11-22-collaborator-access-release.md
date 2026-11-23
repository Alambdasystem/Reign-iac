# Collaborator console permissions: Core and AX paired rollout

## Scope and tested source
- PR #125 merged as `f57916e5ff366917b0536c9acf03fd1c82d2e26c` (per-member, revocable console-access grant).
- PR validation: https://github.com/Alambdasystem/Reign-iac/actions/runs/45007888662 (4/4 succeeded).
- Main validation: https://github.com/Alambdasystem/Reign-iac/actions/runs/45008560846 (4/4 succeeded).
- Gated publication: https://github.com/Alambdasystem/Reign-iac/actions/runs/45009100978 (success, full container acceptance, registry publication).
- Release manifest includes `"provision": false`.

## Exact image changes
| Service | Previous digest | Tested release digest |
| --- | --- | --- |
| Core | `sha256:e05987e4e92adc68548ecc3c0f641feb58e8a642ac428fb2ac7cb850c04ad31a` | `sha256:a355349b3abce5ec3a4e26bfbc0b59d3b6a279198798cdfe44e5611f179262c3` |
| AX | `sha256:1cccbcc4036f2a78bd122e7452799f87317c2c0513623435d31ffdea3070b068` | `sha256:03d151c3856189a3c604718e794dc4aa46cfce1fcdc4125c017c9e9e1cdb6382` |

Both image digests were printed by the same successful gated release job. Preserve Computer digest `sha256:97ce9f567da44aa65069e3a442754b4fa93a61bc3a5d19051911d25c0eb5b5c1` and Temporal digest `sha256:afbae0626e74adbb7f1b4fab54b11342bc11af12df184b0847a5d6c55089aeb4`, all plans, disks, environment variables and configuration. No secrets in source or receipt.

## Authorization and promotion
Core owns durable membership grant and service-token introspection. AX validates the live grant before permitting REIGN/Genesis work-surface requests. Do not grant all employees owner privileges or copy owner credentials. Individual grant defaults off and must be applied by the owner to the VERIFIED Mayur Team member in the member management UI. No account is auto-elevated by promotion. Owner vault, computer sessions, unknown APIs, node and policy mutation remain protected.

After merging, plan existing Render Blueprint `exs-dbofqkm417fc7384kou0`, review exactly TWO `UpdateServiceImage` updates (Core and AX) and no unrelated changes, then approve. Check both live deployments and health before claiming rollout done.
