-- Proposed only; requires separate approved migration and restricted runtime grants.
-- LangGraph needs pending node writes in addition to checkpoints to prevent a
-- successful node from being reexecuted after a different node fails.
CREATE TABLE IF NOT EXISTS reign.checkpoint_writes (
    thread_id uuid NOT NULL,
    checkpoint_id text NOT NULL,
    owner_id text NOT NULL,
    task_id text NOT NULL CHECK (length(task_id) BETWEEN 1 AND 160),
    task_path text NOT NULL DEFAULT '',
    write_index integer NOT NULL,
    channel text NOT NULL CHECK (length(channel) BETWEEN 1 AND 160),
    value_ciphertext bytea NOT NULL,
    value_nonce bytea NOT NULL CHECK (octet_length(value_nonce)=12),
    key_version integer NOT NULL CHECK (key_version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (thread_id,checkpoint_id,task_id,write_index),
    FOREIGN KEY (thread_id,checkpoint_id)
        REFERENCES reign.graph_checkpoints(thread_id,checkpoint_id) ON DELETE CASCADE,
    FOREIGN KEY (thread_id,owner_id)
        REFERENCES reign.conversations(thread_id,owner_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS reign_checkpoint_writes_owner
    ON reign.checkpoint_writes(owner_id,thread_id,checkpoint_id);
ALTER TABLE reign.checkpoint_writes ENABLE ROW LEVEL SECURITY;
ALTER TABLE reign.checkpoint_writes FORCE ROW LEVEL SECURITY;
DO $policy$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='reign' AND policyname='reign_checkpoint_writes_owner') THEN
        CREATE POLICY reign_checkpoint_writes_owner ON reign.checkpoint_writes
            FOR ALL USING (owner_id = NULLIF(current_setting('reign.actor_id',true),''))
            WITH CHECK (owner_id = NULLIF(current_setting('reign.actor_id',true),''));
    END IF;
END $policy$;
-- No GRANTs or runtime migration activation.
