# REIGN owner mailbox pilot

## Deploy configuration

Configure a Google OAuth web application with the Gmail API enabled and a
Microsoft Entra web application for the owner's tenant. Configure Google to
allow the intended user. Request the smallest delegated permissions for
the preview: Gmail metadata and Microsoft Graph User.Read and Mail.ReadBasic.
Check the relevant Google publishing requirements for the app's audience.
In REIGN, open **Work → Accounts → Manage connections**. Select **Configure**
on Gmail or Outlook to paste the OAuth client ID, client secret, and exact
mailbox address. Outlook also requires the Microsoft Entra tenant ID. The
form shows the callback URL. Saving new settings clears the existing mailbox
authorization, and the owner can then select **Connect**.

Client settings and grants are sealed as separate owner- and provider-bound
records on Core's existing private workspace disk. The form is write-only.
There is no field for a Gmail or Outlook account password.

As an alternative, a Core administrator can set `REIGN_MAIL_ORIGIN`,
`REIGN_GMAIL_CLIENT_ID`, `REIGN_GMAIL_CLIENT_SECRET`, `REIGN_GMAIL_OWNER_EMAIL`,
`REIGN_OUTLOOK_CLIENT_ID`, `REIGN_OUTLOOK_CLIENT_SECRET`,
`REIGN_OUTLOOK_TENANT_ID`, and `REIGN_OUTLOOK_OWNER_EMAIL` in the private
service environment. Settings saved in Accounts take priority.

Register these exact HTTPS paths at the respective providers.

- Google `/connections/mail/gmail/callback`
- Microsoft `/connections/mail/outlook/callback`

Review proxy and access-log settings for the Google callback. AX suppresses
Uvicorn access entries for OAuth callbacks. Google returns a one-time code in
the callback query. Any upstream request logs must redact or omit that query.
The clean redirect uses a fragment and the browser removes it after return.

## Consent and verification

1. Sign in as the REIGN pilot owner. Open **Accounts → Manage connections** and select **Connect** for the provider.
2. Approve the provider's consent prompt in the same browser. AX verifies that
   the returning session matches the initiating session and consumes the
   consent attempt before the code exchange.
3. Check the verified mailbox address and choose **Preview unread**. When both
   are verified, **Preview both inboxes** displays their bounded samples
   independently. Counts describe the sampled headers.
4. **Disconnect** removes the local grant and invalidates outstanding consent.
   Manage provider-side consent from the Google or Microsoft account settings.

Core encrypts tokens on the existing private workspace disk with owner and
provider binding. The only mailbox MCP invocation is the owner-authenticated
preview from Connections. It uses an ephemeral loopback credential between
Core and its private MCP process. It is outside the model's ordinary tool
inventory and keeps mailbox content out of shared room context and chat archives.

Provider setup defaults to unavailable. A browser account signed into another
application cannot serve as REIGN's provider grant.
