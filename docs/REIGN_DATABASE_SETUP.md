# REIGN isolated role setup (review before running)

This script is a template for an authorized PostgreSQL administrator connecting
**privately** to the existing Oregon Moby Alpha PostgreSQL instance. It must not
be executed automatically by an agent or through an unrestricted MCP SQL tool.

1. Take and verify a recoverable database backup.
2. Confirm the database has enough free disk and connection capacity.
3. Create a separate `reign_app` login with a randomly generated password
   stored in Render secret settings; never put the password in Git or chat.
4. Run both numbered SQL files in `migrations/` in order under a reviewed
   migration account, then run the grants below as an authorized administrator.
5. Configure `REIGN_DATABASE_URL` privately for Core/MCP only.
6. Verify `/api/database/readyz`, then run isolation tests using a REIGN-only role.
7. Do not modify Moby Alpha's application role, database schema or tables.

```sql
-- Review role name and permissions with the database owner.
-- The login role is created separately with a securely stored password.
GRANT CONNECT ON DATABASE moby_alpha_db TO reign_app;
GRANT USAGE ON SCHEMA reign TO reign_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA reign TO reign_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA reign
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO reign_app;
```

These grants alone do not revoke any previously inherited permissions.
Verify `reign_app` is not a member of privileged Moby roles, cannot access
Moby tables, and cannot create objects outside its dedicated schema.

Temporal pilot persistence stays on its existing disk. REIGN application
tables do not replace Temporal's own database or workflow state.

## Verification

The runtime readiness probe checks all four tables, required table privileges,
role elevation/membership, DDL privileges, and access to other user tables.
Schema USAGE alone is insufficient. If shared PUBLIC grants prevent isolation,
use a separate database; do not revoke existing applications' grants blindly.
Default privileges must be configured for the actual migration/object-owner role.

CI applies both migrations twice to a disposable local PostgreSQL 18 database,
checks constraints, owner-scoped operations and denied access to an unrelated
fixture schema, and verifies readiness rejects missing tables/grants and
elevated roles. These tests never connect to the live Moby Alpha database.
The hosted Render connector currently cannot inspect the live instance because
its external IP allowlist is empty. Preserve that network restriction.
