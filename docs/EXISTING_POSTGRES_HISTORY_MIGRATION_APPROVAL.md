# REIGN history: proposed existing-Postgres migration approval packet

**Status: PREPARED ONLY — no DDL executed, no production history enabled.**
Prepared 2026-11-10. Release owner must review database, migration script, backup, grants, key custody and CI results before approval.

## Existing infrastructure and target selection
Jim's Render workspace currently exposes:
- `moby-alpha-db` (PostgreSQL 18, Oregon, paid `0.1c-256mb`, existing Moby Alpha application database).
- `blueberry-owner-db` (PostgreSQL 16, Ohio, free, scheduled to expire 2026-11-22).
- Core, AX, Computer, and Temporal run in Oregon. **REIGN has no dedicated database.**

**Candidate for review:** isolated `reign` schema and strictly independent `reign_runtime` login on **existing `moby-alpha-db`**. No provisioning or plan change. Oregon locality and nonexpiring plan favor it over the Ohio expiring free database.

**Important:** This is *not* authority to reuse Moby Alpha's database user, change Moby Alpha's tables, share the existing application DSN, or execute DDL. An isolated schema in the same database still shares capacity, backups and failure domain; explicit owner/security approval and capacity assessment are required. If isolation requirements fail, stop and revise the infrastructure decision.

The hosted Render SQL connector refused read-only queries to both databases because their external IP allowlists are empty. **Do not broaden IP allowlists for this exercise.** Perform preflight from approved internal network/Render connection, and capture redacted results only.

## Read-only preflight evidence required (run with admin from approved internal environment)
```sql
SELECT current_database(), current_setting('server_version'), current_user,
       pg_size_pretty(pg_database_size(current_database()));
SELECT nspname FROM pg_namespace
 WHERE nspname IN ('reign','public');
SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls
 FROM pg_roles WHERE rolname LIKE 'reign%';
SELECT relname FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid
 WHERE n.nspname='reign';
```
Confirm free capacity, connection headroom, backup retention and whether a restore drill can meet RTO/RPO **without disrupting Moby Alpha**.

## Migration order (review exact scripts in branch)
1. Confirm required historical migrations `001`–`004` and `005_reign_checkpoint_writes.sql` against actual repo; do not guess filenames or skip a dependency.
2. On a disposable PostgreSQL 18 environment, inspect all DDL for idempotency, owner-bound foreign keys, encryption column types, row-level security forced on every REIGN table and the effective grants.
3. Create an isolated `reign` schema with a migration owner distinct from runtime login. Restrict `reign_runtime` to narrowly scoped schema USAGE and table DML (and required sequences if any); no DB/schema CREATE, no BYPASSRLS and no permissions on Moby Alpha application tables.
4. Require application connections to set `reign.actor_id` from the authenticated server-side principal **inside each transaction**. Prove cross-owner access is denied and owner cannot be provided by a model/client.
5. Take a verified recoverable backup and execute a disposable restore. Prepare forward-fix and reversion actions; irreversible migration elements require a separate explicit review.
6. Record independent reviewer, approved SQL checksum, target database ID, window, backup restore evidence and stop/rollback decision before applying production DDL.

## Encryption rotation stages
The feature branch adds a versioned keyring to `reign/history_crypto.py`. Existing `REIGN_HISTORY_KEY` continues to represent version 1 **only when `REIGN_HISTORY_KEYS_JSON` is entirely absent**. A configured but malformed keyring fails closed instead of falling back.

Secret-manager-only structure (placeholders, NOT real values):
```json
{"active_version":2,"keys":{"1":"<BASE64URL_32_BYTES_OLD>","2":"<BASE64URL_32_BYTES_NEW>"}}
```
- Stage 0: keep production history disabled. Verify existing secrets access is restricted; do not put real secrets in Git, tickets or this document.
- Stage 1: obtain two independent random 32-byte keys under approved custody; verify backup/recovery instructions; if no legacy history exists, a single new version can be used.
- Stage 2: in tests, encrypt under v1, enable keyring with v1+v2 while v2 active, and confirm both versions decrypt and new writes use v2. Check tamper, owner, thread, record, and purpose separation.
- Stage 3: deploy code through normal reviewed CI and release process. Do not activate `REIGN_HISTORY_MODE=postgres` until datastore roles, full saver conformance, backup restoration and live acceptance tests are verified.
- Stage 4: future re-encryption must preserve record AAD and implement transactional row updates with old+new key reads, audit, crash/retry safety and proof that every surviving row was rewrapped. **No re-encryption automation is implemented in this PR. Never delete v1 key merely because v2 is active.**
- Stage 5: retire old key only after independently verified zero old-version rows, rollback window, recovery backup, approved destruction protocol and consent from owner.

## Approval evidence fields
- Target DB: `dpg-dbk48oqjnfac739i4nl0-a` **proposed, not approved**
- Existing application impact: review required
- Exact migration files and SHA-256 digests: pending
- Backup/restore drill and retention: pending
- Runtime identity and RLS negative tests: pending
- Key custody, rotation and recovery: pending
- Four CI checks on exact PR HEAD: pending
- 21 real authenticated AX Work prompt-to-MCP receipts: NOT RUN
- Independent reviewer / owner production migration approval: pending

**Stop conditions:** missing SQL preflight, missing restore drill, any Moby Alpha access grant to REIGN runtime, unexpected schema diffs, key loss, CI failures, missing live acceptance, cost or instance-plan change, or attempted OpenTofu gate bypass.

No production migrations, privileges, secrets, Render resources, image pins, plan changes, automated Blueprint synchronization, PR merges or deployments are authorized by this packet.
