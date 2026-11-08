-- Run only through a reviewed migration with a dedicated REIGN role.
-- Do not run as the Moby Alpha application role.
CREATE SCHEMA IF NOT EXISTS reign;

CREATE TABLE IF NOT EXISTS reign.sessions (
    session_id uuid PRIMARY KEY,
    owner_id text NOT NULL,
    profile_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('opening','active','disconnected','closed')),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sessions_owner_created
    ON reign.sessions (owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS reign.tasks (
    task_id uuid PRIMARY KEY,
    owner_id text NOT NULL,
    session_id uuid REFERENCES reign.sessions(session_id),
    result text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tasks_owner_session
    ON reign.tasks (owner_id, session_id);

-- The deployment role must be explicitly granted access only to reign schema
-- and its tables. No grants on Moby Alpha schemas are part of this migration.
