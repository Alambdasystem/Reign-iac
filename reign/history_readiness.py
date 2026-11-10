"""Read-only, fail-closed readiness for the future durable REIGN history store.

This does NOT enable migrations, allocate paid services, or activate persistence.
Only trusted Core calls this probe; any failure is reduced to an anonymous 503.
"""
from __future__ import annotations

import os
from reign.history_crypto import HistoryCipher
from reign.database import connect_reign_database


async def history_ready() -> bool:
    # Never fall back to the website account-vault key. A configured but invalid
    # history key must reject activation instead of silently using ephemeral state.
    if os.getenv("REIGN_HISTORY_MODE", "disabled") != "postgres":
        return False
    try:
        HistoryCipher.from_env()
    except ValueError:
        return False

    required_tables = ("conversations", "conversation_messages",
                       "graph_checkpoints", "checkpoint_writes", "trace_spans")
    required_policies = {
        "conversations": "reign_conversations_owner",
        "conversation_messages": "reign_messages_owner",
        "graph_checkpoints": "reign_checkpoints_owner",
        "checkpoint_writes": "reign_checkpoint_writes_owner",
        "trace_spans": "reign_traces_owner",
    }
    try:
        async with connect_reign_database() as gateway:
            conn = gateway.connection
            role = await conn.fetchrow("""
                SELECT NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole
                            OR r.rolreplication OR r.rolbypassrls) AS restricted,
                       NOT has_schema_privilege(current_user,'reign','CREATE') AS no_schema_ddl,
                       NOT has_database_privilege(current_user,current_database(),'CREATE')
                         AS no_db_ddl
                FROM pg_roles r WHERE r.rolname=current_user
            """)
            if not role or not all(role.values()):
                return False
            records = await conn.fetch("""
                SELECT c.relname AS name, c.relrowsecurity AS rls,
                       c.relforcerowsecurity AS forced,
                       has_table_privilege(current_user,c.oid,'SELECT,INSERT')
                         AS writable,
                       p.policyname AS policy
                FROM pg_class c
                JOIN pg_namespace n ON n.oid=c.relnamespace
                LEFT JOIN pg_policies p ON p.schemaname=n.nspname
                      AND p.tablename=c.relname
                WHERE n.nspname='reign' AND c.relname=ANY($1::text[])
            """, list(required_tables))
            names = set()
            for row in records:
                table = row["name"]
                if (table not in required_policies or
                        row["policy"] != required_policies[table] or
                        not row["rls"] or not row["forced"] or
                        not row["writable"]):
                    return False
                names.add(table)
            return names == set(required_tables)
    except Exception:
        # No DSN, database metadata, grant details or secrets to HTTP callers.
        return False
