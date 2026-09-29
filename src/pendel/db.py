"""SQLite storage at PENDEL_DATA_DIR with plain-SQL migrations (charter G3).

The schema holds only what G3 needs to schedule and de-duplicate
notifications: users (opaque id, no email or name -- see charter G5),
their saved commutes, their linked notification channels and per-commute
notification state. Migrations are plain `.sql` files under
`migrations/`, applied in filename order and recorded in a
`schema_migrations` table so `migrate` is idempotent.
"""

from __future__ import annotations

import datetime as dt
import os
import secrets
import sqlite3
from pathlib import Path

from pendel.commute import Commute

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def data_dir() -> Path:
    """The data directory from PENDEL_DATA_DIR (charter G3)."""
    return Path(os.environ["PENDEL_DATA_DIR"])


def db_path() -> Path:
    return data_dir() / "pendel.db"


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending `NNNN_*.sql` migrations in order; return newly applied filenames."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "filename TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )
    applied = {row[0] for row in conn.execute("SELECT filename FROM schema_migrations")}
    newly_applied = []
    for path in sorted(migrations_dir.glob("*.sql")):
        if path.name in applied:
            continue
        conn.executescript(path.read_text())
        conn.execute("INSERT INTO schema_migrations (filename) VALUES (?)", (path.name,))
        newly_applied.append(path.name)
    conn.commit()
    return newly_applied


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open a migrated connection with foreign keys enforced (required for
    ON DELETE CASCADE, e.g. delete_user)."""
    target = path if path is not None else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def create_user(conn: sqlite3.Connection) -> str:
    """Create a user with an opaque random id and return it."""
    user_id = secrets.token_urlsafe(16)
    conn.execute("INSERT INTO users (id) VALUES (?)", (user_id,))
    conn.commit()
    return user_id


def delete_user(conn: sqlite3.Connection, user_id: str) -> None:
    """Delete a user and, via ON DELETE CASCADE, every commute, channel and
    notification belonging to them (charter G5: delete my data in one click)."""
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()


def user_exists(conn: sqlite3.Connection, user_id: str) -> bool:
    """Whether `user_id` is a real user id (charter G2: an unknown `uid`
    cookie is treated as no cookie, not an error)."""
    return conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is not None


def add_commute(conn: sqlite3.Connection, user_id: str, commute: Commute) -> int:
    """Insert a saved commute for `user_id` and return its row id."""
    cur = conn.execute(
        "INSERT INTO commutes (user_id, origin_stop_id, destination_stop_id, lines, "
        "weekdays, window_start, window_end, delay_threshold_min) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            commute.origin_stop_id,
            commute.destination_stop_id,
            ",".join(sorted(commute.lines)),
            ",".join(str(day) for day in sorted(commute.weekdays)),
            commute.window_start.strftime("%H:%M"),
            commute.window_end.strftime("%H:%M"),
            commute.delay_threshold_min,
        ),
    )
    conn.commit()
    return cur.lastrowid


def list_commutes(conn: sqlite3.Connection, user_id: str) -> list[tuple[int, Commute]]:
    """Return `(row id, Commute)` pairs for every commute saved by `user_id`."""
    rows = conn.execute(
        "SELECT id, origin_stop_id, destination_stop_id, lines, weekdays, window_start, "
        "window_end, delay_threshold_min FROM commutes WHERE user_id = ? ORDER BY id",
        (user_id,),
    ).fetchall()
    return [
        (
            row["id"],
            Commute(
                origin_stop_id=row["origin_stop_id"],
                destination_stop_id=row["destination_stop_id"],
                lines=frozenset(row["lines"].split(",")),
                weekdays=frozenset(int(day) for day in row["weekdays"].split(",")),
                window_start=dt.time.fromisoformat(row["window_start"]),
                window_end=dt.time.fromisoformat(row["window_end"]),
                delay_threshold_min=row["delay_threshold_min"],
            ),
        )
        for row in rows
    ]
