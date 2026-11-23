# LinkedIn drafting and publishing

REIGN can draft text through `linkedin_preview_post` in the private Workspace MCP catalog.
To publish, open **LinkedIn publishing** in the private Workspace, enter the draft,
and select **Preview post**. Review the exact text and configured author, then select
**Publish publicly**. Publishing accepts one use of a five-minute preview tied to the
owner's signed-in session. Editing the text requires a fresh preview. If LinkedIn
does not confirm delivery, check the account before submitting again.

Configure the approved LinkedIn access token and author URN privately on Core using
`REIGN_LINKEDIN_ACCESS_TOKEN` and `REIGN_LINKEDIN_AUTHOR_URN`. Set
`REIGN_LINKEDIN_VERSION` to the approved Posts API version and
`REIGN_LINKEDIN_PUBLISH_ENABLED=true` after verifying the account and posting permissions.
Publishing is disabled by default. The configured author can be a personal profile
or an organization account. This flow supports public text posts.

[LinkedIn Posts API](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api)
