# LinkedIn publishing MCP tool (opt-in candidate)

This adds `linkedin_preview_post` and `linkedin_publish_post` to the
existing *private* REIGN Workspace MCP catalog, not a new public server.

- Preview is read-only. It validates a 1–3000-character text-only post.
- Publish requires explicit owner authorization in the current task and the
  `approved=true` argument. It is disabled by default.
- Configuration belongs only in the private Core Render environment:
  `REIGN_LINKEDIN_ACCESS_TOKEN`, `REIGN_LINKEDIN_AUTHOR_URN`,
  `REIGN_LINKEDIN_PUBLISH_ENABLED=true`, and optional
  `REIGN_LINKEDIN_VERSION=202611`.
- Set the author URN to an authorized personal profile or organization. The
  model cannot override the server-configured author; one author per Core
  instance. Use LinkedIn's official OAuth flow and appropriate posting
  permissions. Do not place tokens in Git, chat, browser, or screenshots.
- The preview response never includes credentials. Errors do not include
  LinkedIn response bodies or tokens. No retry occurs after uncertain
  publication outcomes; verify the destination first.
- Publishing sends **text-only** posts using the official LinkedIn Posts API.
  Image/video uploads, OAuth connection UI, token refresh, approval receipts,
  per-channel selection, and durable duplicate suppression are **not** in this
  PR and must be implemented before using it for the SOC campaign's two
  media posts. No browser login bypass is introduced.
- Production deployment is separate and must pass existing CI and approval
  gates. No credentials or service settings are changed by merging this PR.

Reference: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
