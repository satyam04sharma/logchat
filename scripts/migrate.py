"""Apply versioned migrations atomically, refusing changed migration history."""
import hashlib
import os
from pathlib import Path
import psycopg

with psycopg.connect(os.environ["MIGRATION_DATABASE_URL"]) as conn:
    # This ledger lives outside the public API schema and contains no application data.
    conn.execute("create schema if not exists logchat_internal")
    conn.execute("create table if not exists logchat_internal.migrations (name text primary key, sha256 text not null)")
    conn.execute("select pg_advisory_xact_lock(180034)")
    for path in sorted(Path("supabase/migrations").glob("*.sql")):
        sql = path.read_text()
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        row = conn.execute("select sha256 from logchat_internal.migrations where name = %s", (path.name,)).fetchone()
        if row:
            if row[0] != checksum:
                raise RuntimeError("An applied migration changed: " + path.name)
            continue
        conn.execute(sql)
        conn.execute("insert into logchat_internal.migrations values (%s, %s)", (path.name, checksum))
        print("Applied " + path.name)

    # Give the private worker a login after the schema exists; no RLS bypass or broad table grants.
    from psycopg.conninfo import conninfo_to_dict
    from psycopg import sql
    password = conninfo_to_dict(os.environ["MIGRATION_DATABASE_URL"])["password"]
    conn.execute(sql.SQL("ALTER ROLE logchat_worker LOGIN PASSWORD {}").format(sql.Literal(password)))
    # Existing REST instances must see newly migrated tables and constraints.
    conn.execute("NOTIFY pgrst, 'reload schema'")
