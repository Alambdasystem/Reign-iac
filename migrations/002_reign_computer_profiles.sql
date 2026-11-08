-- Additive, idempotent REIGN-only computer profile and control state migration.
-- Apply with a dedicated migration role after review; never with Moby Alpha app credentials.
CREATE SCHEMA IF NOT EXISTS reign;
CREATE TABLE IF NOT EXISTS reign.computer_profiles (
    profile_id text PRIMARY KEY,
    owner_id text NOT NULL,
    mode text NOT NULL CHECK (mode IN ('managed', 'external')),
    endpoint_url text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT external_endpoint_required CHECK (
      (mode = 'managed' AND endpoint_url IS NULL) OR
      (mode = 'external' AND endpoint_url IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS computer_profiles_owner ON reign.computer_profiles(owner_id);
CREATE TABLE IF NOT EXISTS reign.control_leases (
    session_id uuid PRIMARY KEY REFERENCES reign.sessions(session_id) ON DELETE CASCADE,
    owner_id text NOT NULL,
    worker_id text NOT NULL,
    epoch bigint NOT NULL DEFAULT 0 CHECK (epoch >= 0),
    mode text NOT NULL CHECK (mode IN ('agent','human')),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS control_leases_owner ON reign.control_leases(owner_id);
-- No grants on non-REIGN schemas. Production credentials and RLS require separate review.
