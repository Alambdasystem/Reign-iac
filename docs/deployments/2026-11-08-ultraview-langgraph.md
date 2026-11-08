# UltraView AX and LangGraph rollout — 2026-11-08

## Verified source

- Application PR: https://github.com/Alambdasystem/Reign-iac/pull/7
- Source revision: `cf95a713ba6c7a6c7f3f3bc79a20f3036517dfaf`
- PR CI: https://github.com/Alambdasystem/Reign-iac/actions/runs/42536113804 — all four checks passed.
- Main CI: https://github.com/Alambdasystem/Reign-iac/actions/runs/42536418580 — all four checks passed.
- Image release: https://github.com/Alambdasystem/Reign-iac/actions/runs/42536592394 — passed.
- 55 Python tests; disposable PostgreSQL migration/permission tests; four-container
  browser acceptance including 390/430/1280 layouts, restored sessions, fresh
  frames, takeover/handback, stale-command fencing, reconnect and worker restart.
- PR screenshot artifact `13639248500`, SHA-256
  `4d19225f675b1c17a094d9d7661a764cc9ab71b8cab4c2552886aef126f0162a` inspected.
  This artifact tests GitHub's temporary PR merge revision, not a production deploy.

## Promotion scope

Existing Blueprint `exs-dbofqkm417fc7384kou0` remains the only deployment owner,
with autoSync=false, main branch and deploy/render-pilot.proposed.yaml. Promote
only the tested Core and AX image digests. Computer and Temporal keep their
existing immutable images. Resource sizes and approved base price do not change.

Core references existing workmates-api OPENAI_API_KEY and OPENAI_MODEL inside
Render. Workmates is not adopted, changed or redeployed by this Blueprint. No
secret value is copied into source, AX, chat responses, logs or browser storage.
The model endpoint is OpenAI's `/v1/responses`, matching Workmates' implementation.

The temporary AX startup acceptance command exercises the public authenticated
API, actual OpenAI tool calls, LangGraph checkpoints, human takeover and explicit
resume through Core/Temporal/Computer. It creates/deletes its own browser session
and conversation, logs only a sanitized receipt, and must be removed after the
receipt is collected. CI uses its exact local-only mode without a provider key.

## Live verification

Passed at 2026-11-08T23:54:42Z. Sanitized receipt:
[2026-11-08-ultraview-acceptance.json](2026-11-08-ultraview-acceptance.json).

- Promotion PR8 merged as edd6418d3444eb824ca0f59b609ecc27fc6a8a97.
- Render sync exe-dbogm3jrjlhs73bcvprg updated only Core and AX.
- Core deploy dep-dbogm7jrjlhs73bd07eg is live on the tested digest.
- AX verification deploy dep-dbogn9u417fc73873f6g used the same tested digest.
- Actual OpenAI model: gpt-6-astra, inherited from Workmates. workspace_status
  and browser_status tools completed; the first turn used 1,647 input and 102
  output tokens. A separate actual-model navigation paused under human control,
  rejected resume before handback, then resumed through Temporal successfully.
- Public authentication/CSRF, fresh frames, takeover/handback, stale-epoch
  rejection and cleanup all passed. The verifier deleted its own conversation
  and browser session.
- First verification hit an old AX instance (404 /api/me) during the rolling
  update. A same-image AX redeploy after both new services were live passed.
- The public browser showed the new UltraView sign-in page. Authenticated UI
  interactions were exercised by CI; the live run exercised the authenticated
  API. Human takeover signoff has not been performed by James.

The Blueprint now specifies normal AX startup without the temporary verification
command. This cleanup changes no images, credentials, resource sizes or data.

## Remaining integrations

Voice, external desktop adapters, durable LangGraph checkpoints and restricted
REIGN database credentials remain unfinished. Current graph memory is bounded
and process-local; Temporal history uses the existing persistent disk. The shared
database and unrelated services remain unchanged.

## Rollback

Review and apply a Blueprint reverting Core/AX to the last accepted digests:

- Core: `ghcr.io/alambdasystem/reign-core@sha256:1259e7c738a11f8385f059c754b1f153829d80d89dbe16bf24a74b710e4d1915`
- AX: `ghcr.io/alambdasystem/reign-ax@sha256:d9c480181f432e3ec26971d530fa3e0c6cf0c5975b77d15f8534b98b97ad4142`

Restore AX's normal startup command without the acceptance invocation. Rollback
loses process-local conversation checkpoints; it does not restore browser
sessions or undo completed browser actions.

## Promoted image pins

- core: `ghcr.io/alambdasystem/reign-core@sha256:2e6f8eb970480b0e02b7c901a119f71687de984640cb5fb1b8f320817c4b1cd5`
- ax: `ghcr.io/alambdasystem/reign-ax@sha256:661590b743bcaed777fb8f17f3142e44fccce034543bddeca44f21ec1ef53543`

Release artifact `13639821572`, SHA-256 `cc57496eb0a1d94d757aa03010e3ef2904a999268ae8d06fb49a4d454b3f3f6a`, verified before promotion.
