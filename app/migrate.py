"""Hand-rolled migration runner.

Applies migrations/NNN_name.sql in order. The applied version is stored in
SQLite's PRAGMA user_version, so there is no bookkeeping table.
"""
from pathlib import Path


def migrate(conn, migrations_dir):
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    applied = []
    for path in sorted(Path(migrations_dir).glob("[0-9][0-9][0-9]_*.sql")):
        version = int(path.name[:3])
        if version <= current:
            continue
        script = f"BEGIN;\n{path.read_text()}\nPRAGMA user_version = {version};\nCOMMIT;"
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        applied.append(version)
    return applied
