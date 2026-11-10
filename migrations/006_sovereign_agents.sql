-- Sovereign registry storage prototype; migration requires independent review.
-- Existing REIGN runtime is deliberately not wired to these tables.
CREATE SCHEMA IF NOT EXISTS reign;

CREATE TABLE IF NOT EXISTS reign.sovereign_agents (
    tenant_id text NOT NULL CHECK (tenant_id ~ '^[a-z][a-z0-9-]{1,62}$'),
    agent_id text NOT NULL CHECK (agent_id ~ '^[a-z][a-z0-9-]{1,62}$'),
    owner_id text NOT NULL CHECK (owner_id ~ '^[a-z][a-z0-9-]{1,62}$'),
    display_name text NOT NULL CHECK (length(trim(display_name)) BETWEEN 1 AND 120),
    harness text NOT NULL CHECK (harness IN ('langgraph','external')),
    provider text NOT NULL CHECK (provider IN ('render','aws','azure','external')),
    constitution_version text NOT NULL CHECK (length(constitution_version) BETWEEN 1 AND 80),
    capabilities text[] NOT NULL DEFAULT ARRAY[]::text[],
    parent_id text,
    lifecycle text NOT NULL DEFAULT 'draft' CHECK (lifecycle IN ('draft','active','suspended','retired')),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT sovereign_agents_pk PRIMARY KEY (tenant_id, agent_id),
    CONSTRAINT sovereign_parent_not_self CHECK (parent_id IS NULL OR parent_id <> agent_id),
    CONSTRAINT sovereign_parent_same_tenant FOREIGN KEY (tenant_id, parent_id)
        REFERENCES reign.sovereign_agents (tenant_id, agent_id) ON DELETE RESTRICT,
    CONSTRAINT sovereign_capability_shape CHECK (
        array_position(capabilities, NULL) IS NULL
        AND array_to_string(capabilities, ',') !~ '[^a-z0-9_.,:-]'
    )
);
CREATE INDEX IF NOT EXISTS sovereign_agents_owner_idx
    ON reign.sovereign_agents (tenant_id, owner_id, lifecycle);
-- Defense in depth: PostgreSQL row filters are not a replacement for a verified
-- identity gateway. The runtime DB role must not be able to set identity context
-- directly; production grant design must be independently audited.
ALTER TABLE reign.sovereign_agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE reign.sovereign_agents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS sovereign_tenant_policy ON reign.sovereign_agents;
CREATE POLICY sovereign_tenant_policy ON reign.sovereign_agents
    USING (tenant_id = current_setting('sovereign.tenant_id', true)
       AND owner_id = current_setting('sovereign.owner_id', true))
    WITH CHECK (tenant_id = current_setting('sovereign.tenant_id', true)
       AND owner_id = current_setting('sovereign.owner_id', true));
