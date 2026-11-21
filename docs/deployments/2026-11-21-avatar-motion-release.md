# Avatar motion release — 2026-11-21

## Tested source

[PR #99](https://github.com/Alambdasystem/Reign-iac/pull/99) replaces wall-clock
pose jumps and hard frame swaps with bounded pose steps and motion-aware display
interpolation between real neural endpoints. The selected source is
`fd7cee4d0afbd047b23571fa4b2bc3b4092fb721`.

[Source validation](https://github.com/Alambdasystem/Reign-iac/actions/runs/44882047482)
and [main validation](https://github.com/Alambdasystem/Reign-iac/actions/runs/44882657078)
passed all four required checks. The
[gated release](https://github.com/Alambdasystem/Reign-iac/actions/runs/44883228999)
tested the exact images before publishing and succeeded on that source.

The release artifact is
`tested-release-fd7cee4d0afbd047b23571fa4b2bc3b4092fb721`, ID `14699195742`,
4,434,235 bytes. Its downloaded ZIP matched GitHub's SHA-256:

`5747416833907b905cc30c191f9ce9851c54aa7df738b932ec68866ce63361d2`

Release provenance, four-service acceptance and both controlled workspace
receipts identify the selected source. The actual-model browser receipt passed:

- Exact settled neural endpoints for neutral, mouth, pose, smile and a new crop.
- 190 changing intermediate samples between real neural frames.
- Automatic motion presented two new neural endpoints with 448 interpolated
  presentations, with yaw steps bounded to 1.2 degrees.
- At most one real worker and one pending render; crop invalidated old blends.
- Logout terminated the worker and released presentation/source buffers.
- Named-account isolation, CSRF, cookie replay denial and an independent owner
  session remained valid.

These results demonstrate interpolation and bounded queues, not a claim of
30 neural frames per second or physical-device performance. CPU model inference
can still be slow; the display now blends between available poses.

## Promotion scope

Reuse Blueprint `exs-dbofqkm417fc7384kou0` with auto-sync disabled. Promote only
the existing AX image to:

`ghcr.io/alambdasystem/reign-ax@sha256:01fe5ce06eb5a13f6e54e23fd67835520f9ec5ec7491ea10e1ee0460d91ce7e7`

All other image pins, plans, instance counts, disks, environment configuration
and resources remain at the reviewed baseline. This receipt selects the tested
release; Render's deployment history establishes the subsequent live rollout.
No credential is included in Git or the artifact.
