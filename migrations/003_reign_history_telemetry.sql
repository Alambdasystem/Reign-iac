-- Proposal only. Not applied by CI, Render, or this PR.
-- Additive, REIGN-only persistence foundation; use reviewed migration role.
-- No grants to runtime users here. Never use Moby Alpha application credentials.
CREATE SCHEMA IF NOT EXISTS reign;

CREATE TABLE IF NOT EXISTS reign.conversations (
    thread_id uuid PRIMARY KEY,
    owner_id text NOT NULL,
    title text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'idle'
        CHECK (status IN ('idle','running','paused','completed','failed','archived')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    archived_at timestamptz
);
CREATE INDEX IF NOT EXISTS reign_conversations_owner_recent
    ON reign.conversations(owner_id, updated_at DESC);

-- Body must be encrypted *before* writing it; plaintext messages, tool args,
-- prompts, browser screenshots and secrets MUST NOT enter this table.
CREATE TABLE IF NOT EXISTS reign.conversation_messages (
    message_id uuid PRIMARY KEY,
    thread_id uuid NOT NULL REFERENCES reign.conversations(thread_id) ON DELETE CASCADE,
    owner_id text NOT NULL,
    ordinal bigint NOT NULL CHECK (ordinal >= 0),
    role text NOT NULL CHECK (role IN ('system','user','assistant','tool')),
    body_ciphertext bytea NOT NULL,
    body_nonce bytea NOT NULL CHECK (octet_length(body_nonce)=12),
    key_version integer NOT NULL CHECK (key_version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(thread_id, ordinal)
);
CREATE INDEX IF NOT EXISTS reign_messages_thread_order
    ON reign.conversation_messages(thread_id, ordinal);

-- LangGraph checkpoint blobs require authenticated encryption, an explicit
-- serializer allowlist, and per-thread authorization before this is activated.
CREATE TABLE IF NOT EXISTS reign.graph_checkpoints (
    thread_id uuid NOT NULL REFERENCES reign.conversations(thread_id) ON DELETE CASCADE,
    checkpoint_id text NOT NULL,
    owner_id text NOT NULL,
    parent_checkpoint_id text,
    state_ciphertext bytea NOT NULL,
    state_nonce bytea NOT NULL CHECK (octet_length(state_nonce)=12),
    key_version integer NOT NULL CHECK (key_version > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (thread_id, checkpoint_id)
);
CREATE INDEX IF NOT EXISTS reign_checkpoints_thread_recent
    ON reign.graph_checkpoints(thread_id, created_at DESC);

-- Safe structured spans only. Do not store unfiltered model input/output,
-- reasoning, passwords, vault grants, browser forms, request headers or URLs
-- with query strings. Enforce a field allowlist at the ingestion boundary.
CREATE TABLE IF NOT EXISTS reign.trace_spans (
    trace_id uuid NOT NULL,
    span_id text NOT NULL CHECK (span_id ~ '^[0-9a-f]{16}$'),
    parent_span_id text CHECK (parent_span_id IS NULL OR parent_span_id ~ '^[0-9a-f]{16}$'),
    owner_id text NOT NULL,
    thread_id uuid REFERENCES reign.conversations(thread_id) ON DELETE SET NULL,
    service text NOT NULL CHECK (service IN ('ax','core','computer','temporal','mcp')),
    operation text NOT NULL,
    tool_name text,
    outcome text NOT NULL CHECK (outcome IN ('ok','error','blocked','cancelled')),
    duration_ms integer CHECK (duration_ms >= 0),
    input_tokens integer CHECK (input_tokens >= 0),
    output_tokens integer CHECK (output_tokens >= 0),
    error_code text,
    safe_attributes jsonb NOT NULL DEFAULT '{}'::jsonb
       CHECK (jsonb_typeof(safe_attributes)='object'),
    started_at timestamptz NOT NULL,
    PRIMARY KEY (trace_id, span_id)
);
CREATE INDEX IF NOT EXISTS reign_trace_spans_owner_time
    ON reign.trace_spans(owner_id, started_at DESC);
CREATE INDEX IF NOT EXISTS reign_trace_spans_thread_time
    ON reign.trace_spans(thread_id, started_at DESC);

-- RLS is enabled and forced; without reviewed per-owner policies and a
-- restricted runtime role, the application MUST fail closed.
ALTER TABLE reign.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE reign.conversations FORCE ROW LEVEL SECURITY;
ALTER TABLE reign.conversation_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE reign.conversation_messages FORCE ROW LEVEL SECURITY;
ALTER TABLE reign.graph_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE reign.graph_checkpoints FORCE ROW LEVEL SECURITY;
ALTER TABLE reign.trace_spans ENABLE ROW LEVEL SECURITY;
ALTER TABLE reign.trace_spans FORCE ROW LEVEL SECURITY;
-- Runtime grants, owner policies, migration execution and key management
-- are reserved for a separate, tested PR and explicit deployment review.
