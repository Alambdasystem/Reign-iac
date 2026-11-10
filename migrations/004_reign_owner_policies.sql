-- Proposal only. Review and test before ANY application migration.
-- Owner identity is set transaction-locally by the authenticated Core server,
-- never accepted from an MCP tool, prompt, or untrusted HTTP body.
-- Production runtime privileges require a separate reviewed grant operation.
DO $policy$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'reign' AND policyname = 'reign_conversations_owner') THEN
        CREATE POLICY reign_conversations_owner ON reign.conversations
            FOR ALL USING (owner_id = NULLIF(current_setting('reign.actor_id', true), ''))
            WITH CHECK (owner_id = NULLIF(current_setting('reign.actor_id', true), ''));
    END IF;
END $policy$;

DO $policy$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'reign' AND policyname = 'reign_messages_owner') THEN
        CREATE POLICY reign_messages_owner ON reign.conversation_messages
            FOR ALL USING (owner_id = NULLIF(current_setting('reign.actor_id', true), ''))
            WITH CHECK (owner_id = NULLIF(current_setting('reign.actor_id', true), ''));
    END IF;
END $policy$;

DO $policy$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'reign' AND policyname = 'reign_checkpoints_owner') THEN
        CREATE POLICY reign_checkpoints_owner ON reign.graph_checkpoints
            FOR ALL USING (owner_id = NULLIF(current_setting('reign.actor_id', true), ''))
            WITH CHECK (owner_id = NULLIF(current_setting('reign.actor_id', true), ''));
    END IF;
END $policy$;

DO $policy$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname = 'reign' AND policyname = 'reign_traces_owner') THEN
        CREATE POLICY reign_traces_owner ON reign.trace_spans
            FOR ALL USING (owner_id = NULLIF(current_setting('reign.actor_id', true), ''))
            WITH CHECK (owner_id = NULLIF(current_setting('reign.actor_id', true), ''));
    END IF;
END $policy$;

-- No GRANT, BYPASSRLS, login role, SECURITY DEFINER, or elevated privilege.
-- The restricted REIGN runtime must be incapable of directly setting another
-- actor except via its trusted, authenticated application boundary.
