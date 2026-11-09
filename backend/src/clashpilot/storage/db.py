"""SQLite access: one connection per request and a generic numbered-migration runner.

Migration files live in `migrations/*.sql`. The runner applies every file whose name is absent
from `schema_migrations`, whatever its number, so files delivered out of order by parallel
tasks are never skipped. Files must not contain transaction control: each one runs in its own
transaction together with its `schema_migrations` row.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DB_FILENAME = "clashpilot.db"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_MIGRATION_LOCK = threading.Lock()


def connect(data_dir: Path) -> sqlite3.Connection:
    """Open the database of `data_dir` (created if needed) with WAL and foreign keys on."""
    data_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(data_dir / DB_FILENAME, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def migrate(connection: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every migration file missing from `schema_migrations`; return the applied names."""
    with _MIGRATION_LOCK:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.commit()
        done = {row[0] for row in connection.execute("SELECT name FROM schema_migrations")}
        applied: list[str] = []
        for path in sorted(migrations_dir.glob("*.sql"), key=lambda p: p.name):
            if path.name in done:
                continue
            name = path.name.replace("'", "''")
            script = (
                f"BEGIN;\n{path.read_text(encoding='utf-8')}\n;"
                f"\nINSERT INTO schema_migrations (name) VALUES ('{name}');\nCOMMIT;"
            )
            try:
                connection.executescript(script)
            except sqlite3.Error:
                connection.rollback()
                raise
            applied.append(path.name)
        return applied


@contextmanager
def open_db(data_dir: Path, migrations_dir: Path = MIGRATIONS_DIR) -> Iterator[sqlite3.Connection]:
    """Per-request connection, migrated, committed on success, rolled back on error."""
    connection = connect(data_dir)
    try:
        migrate(connection, migrations_dir)
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
