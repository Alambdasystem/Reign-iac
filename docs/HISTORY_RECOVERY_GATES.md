# REIGN transcript recovery gate — draft, not production evidence

## Exact scope
REIGN Core runs a single-instance encrypted completed-turn transcript archive on its existing private 1 GB disk at `/var/data/reign-vault/reign-history.sqlite3`. This is **not** LangGraph mid-action checkpointing, active-tool replay or durable browser execution. No Moby Alpha/Blueberry database or new paid service is authorized.

The disk is present in Render's Core service metadata and `REIGN_HISTORY_MODE=disk` is declared in the live Blueprint; no independent filesystem inspection, backup inventory, encryption-key escrow proof or restore drill has been performed through the available connector. The historical live Core deployment is not itself a backup or restart-recovery receipt. Do not mark this gate passed based on CI.

## Before reliance on durability
1. Confirm single existing Core disk and owner, mount, private permissions, free space and current image digest through authorized Core shell. Do not copy keys or transcript bodies into chat, logs, artifacts, GitHub, or screenshots.
2. Confirm the separate `REIGN_HISTORY_KEY` is present and valid through a non-disclosing readiness check. Establish tested off-service custody of the key and a documented restore procedure approved by the owner; rendering a generated value in logs or Git is prohibited. The account vault key does not substitute.
3. Inventory Render disk snapshots and retention, then create a protected backup to approved access-controlled storage. SQLite must be copied with its online Backup API (not `cp` while writes are active), or with Core stopped during a consistent snapshot. Preserve private file mode; encrypt the backup again at the storage boundary.
4. In an isolated nonproduction environment and using a *separately controlled* key, restore an authorized backup, verify SQLite `PRAGMA integrity_check`, decrypt a consented fixture, reject the wrong key, and verify list/reopen/delete. No real conversation content should go into CI.
5. Run a controlled Core restart against the authorized test thread, verify completed-turn continuity and that previously completed tool calls do not replay. Capture authenticated AX Work traces without secrets and the exact image/disk/test times.
6. Confirm retention/deletion, backup rotation, recovery owner, failure policy, and disk-space alerting before claiming operational recoverability.

## Automated evidence in this branch
`tests/test_disk_history_backup.py` simulates SQLite's consistent online backup, deletion of the original archive, private restore, successful decrypt with the original synthetic key, and failure with an incorrect key; it also rejects world-readable restored files. It **does not** prove live Render snapshots, key escrow, retention, usable production backups, provider availability, full LangGraph checkpoint replay, or the 21 authenticated prompt-to-tool tests. No runtime/Blueprint/credential/resource changes accompany this test.

Do not enable Genesis draft provisioning based on this gate; Genesis's preview policy and trust boundary are separate.
