# Workspace call and conversation layout

The shared Workspace at `/team` uses a light conversation layout with a resizable
REIGN portrait. This is a presentation layer over the existing room, voice,
portrait, camera and group-call controllers. It doesn't create another call
transport or change room authorization.

## Layout and navigation

- Call settings are at the upper left; rooms and workspace tools are at the upper
  right. The bottom row places End on the left, microphone beside it, and the
  message composer on the right. The add/tools control is at the composer's
  right edge.
- The reference used a 709 × 1536 raster. Its top circular controls correspond
  to about 45 CSS pixels and its bottom controls to 49 CSS pixels at a 390-pixel
  phone width. The application uses responsive layout and browser safe areas,
  rather than scaling a fixed phone canvas or drawing imitation operating-system
  bars.
- The rooms menu contains rooms and people, documents, the shared browser, and
  the original account controls. Call settings contain the existing REIGN and
  group-call controls. The add/tools panel exposes explicit camera, dictation,
  and view-sharing actions.
- Existing `Send` and `Ask REIGN` controls preserve the distinction between
  posting a room message and inviting the assistant to answer.

## View behavior

The portrait supports three views: **Chat**, **Together**, and **Face**. Dragging
down enlarges it; dragging up restores the conversation. Tapping cycles views.
The settings panel also provides explicit view buttons. Arrow keys change the
view, Home returns to Chat, and End selects Face when the portrait is focused.

In Face view, the transcript fades out and is removed from keyboard navigation
and the accessibility tree. Returning to Chat restores it. The original canvas,
audio element, participant videos, media streams, conversation nodes, composer,
and draft remain mounted. A view change must not call media start/stop methods,
negotiate another peer connection, acquire another microphone, replay saved
speech, or change rooms.

The main microphone and End controls delegate to the original enabled controls.
When REIGN voice and a group call are both active, the shared microphone control
mutes every active microphone; it unmutes them only when all are already muted.
End leaves both calls. Capability and busy-state restrictions still come from
the underlying controllers.

## Implementation and verification

`services/ax/team-callspace.js` exports `createCallspaceWorkspace` and
`callspaceModeForProgress`. The initializer is idempotent and moves existing
nodes rather than copying or replacing them. `destroy()` restores their original
positions and removes the layout's observers and event listeners.

`services/ax/team-callspace.css` supplies the light visual treatment. Both assets
are served by the existing same-origin static route with revalidation; no new
external script, runtime dependency, server route, or relaxed content-security
policy is required.

The workspace Node test glob includes `tests/team_callspace.test.mjs`. Existing
container/browser acceptance uses public drawer controls to exercise room,
document, camera, account, speech and group-call behavior. The continuous-voice
fixture also switches views while real loopback WebRTC media is active and
checks that nodes, streams, drafts, transcript position and the connection are
preserved. These are controlled acceptance fixtures, not proof of a particular
phone's physical speaker route or camera quality.

Release through the normal validation and gated image-publishing workflows.
Promote only a reviewed tested AX digest in the existing Render Blueprint,
preserving fresh-main pins and settings for the other services. Record the
actual source, acceptance artifact, digest and provider deployment in a separate
deployment receipt.
