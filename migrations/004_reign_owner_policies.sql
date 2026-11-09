-- Proposal only. Review and test before ANY application migration.
-- Owner identity is set transaction-locally by the authenticated Core server,
-- never accepted from an MCP tool, prompt, or untrusted HTTP body.
-- Production runtime privileges require a separate reviewed grant operation.
CREATE POLICY reign_conversations_owner ON reign.conversations
    FOR ALL USING (owner_id = NULLIF(current_setting('reign.actor_id', true), ''))
    WITH CHECK (owner_id = NULLIF(current_setting('reign.actor_id', true), ''));

CREATE POLICY reign_messages_owner ON reign.conversation_messages
    FOR ALL USING (owner_id = NULLIF(current_setting('reign.actor_id', true), ''))
    WITH CHECK (owner_id = NULLIF(current_setting('reign.actor_id', true), ''));

CREATE POLICY reign_checkpoints_owner ON reign.graph_checkpoints
    FOR ALL USING (owner_id = NULLIF(current_setting('reign.actor_id', true), ''))
    WITH CHECK (owner_id = NULLIF(current_setting('reign.actor_id', true), ''));

CREATE POLICY reign_traces_owner ON reign.trace_spans
    FOR ALL USING (owner_id = NULLIF(current_setting('reign.actor_id', true), ''))
    WITH CHECK (owner_id = NULLIF(current_setting('reign.actor_id', true), ''));

-- No GRANT, BYPASSRLS, login role, SECURITY DEFINER, or elevated privilege.
-- The restricted REIGN runtime must be incapable of directly setting another
-- actor except via its trusted, authenticated application boundary.
