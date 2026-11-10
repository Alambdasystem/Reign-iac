# REIGN persistent-history activation / key-lifecycle release gate (2026-11-10)

Status: **BLOCKED; not approved for production**. This is an executable review checklist, not permission to apply migrations or enable history.

## Current evidence
- Core is shipped with `InMemorySaver` by default. `reign/history_readiness.py` returns false unless `REIGN_HISTORY_MODE=postgres`, a valid separate history key and the full restricted-RLS schema exist.
- `reign/history_crypto.py` encrypts with AES-256-GCM but accepts only `key_version=1`; **online key rotation / a versioned decrypt keyring does not exist**. Replacing `REIGN_HISTORY_KEY` would make previously encrypted rows unreadable. **Do not rotate or delete this key until the keyring and migration are implemented and tested.**
- `reign/langgraph_saver.py` says prototype/not wired into production. A migration of tables or setting an environment flag alone **does not** provide durable Core checkpoints.
- Render workspace inventory on 2026-11-10 shows four REIGN services but no dedicated REIGN PostgreSQL database. The existing named PostgreSQL instances belong to unrelated applications; do not reuse their application roles or change their data without a separately reviewed isolation plan.
- `docs/LIVE_PROMPT_ACCEPTANCE_MATRIX.md` defines L01-L21; live authenticated execution is still NOT RUN. A health endpoint, fixtures or synthetic CI cannot substitute for UI evidence.

## Gates before migration approval
- [ ] Confirm dedicated/reviewed REIGN database or an explicitly approved isolated schema in an existing database, with capacity/cost/backup and failure-domain assessment.
- [ ] Record database owner, region, restore point, backup retention and successful disposable restore drill. No production DDL without explicit migration-approval record and reviewed down/forward recovery plan.
- [ ] Review migrations in order, including `005_reign_checkpoint_writes.sql`, ownership constraints, foreign keys, indexes, forced RLS and grant revocations. Test on disposable PostgreSQL with production-matching major version.
- [ ] Create two distinct roles: migration role with narrowly scoped DDL and runtime role that cannot create schema/tables, bypass RLS or access other app tables. Revoke PUBLIC defaults and assert negative privilege tests.
- [ ] Bind `reign.actor_id` from an authenticated AX/Core server-side principal inside each transaction. Never accept an owner ID supplied by the model, request body or unverified client metadata.
- [ ] Source `REIGN_DATABASE_URL` from a restricted runtime identity, over verified TLS as appropriate to the connection environment. Never place URL/password in GitHub issue, CI output, screenshots or this runbook.
- [ ] Obtain independent security review and explicit migration approval before applying any migration.

## Encryption-key lifecycle
- [ ] Provision `REIGN_HISTORY_KEY` independently of account-vault keys; use a cryptographically random 32-byte value and a supported secret manager. No plaintext in Git history, logs, test receipts, chat or ticket.
- [ ] Build a versioned decrypt keyring first: old ciphertext remains decryptable under its stored `key_version`, new writes use a separate active version. Reject missing/unknown key versions, invalid AAD, mixed-owner records and accidental fallback.
- [ ] Test encryption tamper rejection, owner/thread/purpose binding, backup restore and mixed-key decryption on disposable data.
- [ ] Rotate in phases: add new decrypt key; switch write version; test old and new reads; re-encrypt/rewrite old records with audit and checkpoint resume tests; verify all rows migrated; only then revoke/archive old decrypt key per approved retention policy.
- [ ] Specify secret access controls, key custody, recovery backup, revocation and emergency response. Keep account-vault key independent.
- [ ] Do **not** set `REIGN_HISTORY_MODE=postgres` until readiness and runtime conformance pass.

## Runtime activation and acceptance
- [ ] Finish production-grade connection pooling, full LangGraph saver conformance (checkpoint namespaces, concurrent pending writes, ordering, retention/delete) and Core injection with fail-closed behavior.
- [ ] Pass exact-HEAD four CI gates: infra/OpenTofu validation, disposable PostgreSQL integration, Python tests, four-container acceptance. No OpenTofu apply or approval bypass.
- [ ] Run a real graph interrupted/resumed over restart with no replay of previously completed browser effects, and prove owner separation.
- [ ] Execute L01–L21 **by typing prompts in the authenticated AX Work UI** on the exact deployed image digests. Each receipt must include UTC timestamp, selected MCP tool, actual effect, thread/run/workflow IDs, redacted screenshot/log reference, PASS/FAIL/BLOCKED/NOT_RUN. Do not mislabel a mock, health check, or API-only test as live.
- [ ] Only after approval, plan the existing paused Render Blueprint, review the exact diff for changes/price/secret scope, approve the reviewed plan, and verify the deployed image + readiness. Keep `autoSync=false` unless separately authorized.

## Stop conditions
If the production database identity, backup restore evidence, authorized live browser session, exact-image CI, or security review is missing, **stop and leave history disabled**. No new paid services, unrelated database changes, secrets, migrations, merges, deploys or OpenTofu approvals are authorized by this document.
