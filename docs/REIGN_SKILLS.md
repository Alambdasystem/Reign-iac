# REIGN Skills and MCP

Open **Skills** from the private console or the Workspace menu. Pin a skill to
move it to the top, expand it to see its steps, or follow **View connections**
to see which integrations need setup. **Your checklist** can accept a line at
a time or a pasted list. Progress saves to the signed-in user's workspace.

| Skill | User flow | Tool behavior |
| --- | --- | --- |
| Email briefing | Connect and verify Outlook and Gmail individually. Review urgency, unread counts and replies. | The plan identifies required connections and distinguishes sampled minimums from mailbox totals. A mailbox reader needs its own approved provider adapter and grant. |
| AWS sign-in | Select an approved profile, complete provider sign-in and verify the account and role. | The plan describes the human handoff. An approved AWS Identity Center adapter is required for live verification. |
| Action checklist | Draft items, paste them into Skills, check off completed work and copy the list. | MCP prepares a draft. The signed-in UI saves only after a user action. |

Core publishes the fixed MCP schemas `reign_skill_catalog`, `reign_skill_plan`
and `reign_checklist_prepare`. They return catalog metadata, planned steps and
checklist drafts. Private chat and the shared room assistant use that same
allowlist for skill discovery and drafting. A plan reports setup required whenever the catalog has no live
adapter. Adding a provider card or pinning a skill does not grant account access.

AX forwards personal Skills requests through the existing authenticated team
gateway. Core derives the user from its signed actor assertion, rechecks
membership, and encrypts the saved record on the workspace disk. The
revision prevents one tab from replacing a newer save by another tab.

Provider verification, OAuth credential custody and per-call authorization
follow the existing [connection registry design](CONNECTION_REGISTRY_DESIGN.md).
