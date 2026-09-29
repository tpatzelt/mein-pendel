from __future__ import annotations

import sqlite3

import pytest

from pendel import db


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PENDEL_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def conn(data_dir):
    connection = db.connect()
    yield connection
    connection.close()


def _insert_commute(conn: sqlite3.Connection, user_id: str) -> int:
    cur = conn.execute(
        "INSERT INTO commutes (user_id, origin_stop_id, destination_stop_id, "
        "lines, weekdays, window_start, window_end) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, "900000100001", "900000200002", "S41", "0,1,2,3,4", "07:30", "08:00"),
    )
    conn.commit()
    return cur.lastrowid


def _insert_channel(conn: sqlite3.Connection, user_id: str) -> int:
    cur = conn.execute(
        "INSERT INTO channels (user_id, kind, target) VALUES (?, 'ntfy', 'topic-abc')",
        (user_id,),
    )
    conn.commit()
    return cur.lastrowid


def _insert_notification(conn: sqlite3.Connection, commute_id: int) -> int:
    cur = conn.execute(
        "INSERT INTO notifications (commute_id, disruption_key, state, first_notified_at) "
        "VALUES (?, 'cancellation:S41', 'active', datetime('now'))",
        (commute_id,),
    )
    conn.commit()
    return cur.lastrowid


def test_db_path_is_under_pendel_data_dir(data_dir):
    assert db.db_path() == data_dir / "pendel.db"


def test_connect_creates_db_file(data_dir, conn):
    assert (data_dir / "pendel.db").exists()


def test_connect_enables_foreign_keys(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrate_records_initial_migration(conn):
    rows = conn.execute("SELECT filename FROM schema_migrations").fetchall()
    assert [row[0] for row in rows] == ["0001_initial.sql"]


def test_migrate_is_idempotent(conn):
    newly_applied = db.migrate(conn)
    assert newly_applied == []
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1


def test_migration_file_exists():
    assert (db.MIGRATIONS_DIR / "0001_initial.sql").exists()


def test_users_table_has_no_email_or_name_columns(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    assert "email" not in columns
    assert "name" not in columns


def test_create_user_returns_opaque_id(conn):
    user_id = db.create_user(conn)
    row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    assert row is not None
    assert row["id"] == user_id


def test_create_user_ids_are_unique(conn):
    first = db.create_user(conn)
    second = db.create_user(conn)
    assert first != second


def test_notifications_enforce_unique_commute_and_disruption(conn):
    user_id = db.create_user(conn)
    commute_id = _insert_commute(conn, user_id)
    _insert_notification(conn, commute_id)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_notification(conn, commute_id)


def test_delete_user_cascades_to_commutes_channels_and_notifications(conn):
    user_id = db.create_user(conn)
    commute_id = _insert_commute(conn, user_id)
    _insert_channel(conn, user_id)
    _insert_notification(conn, commute_id)

    db.delete_user(conn, user_id)

    assert conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone() is None
    assert (
        conn.execute(
            "SELECT * FROM commutes WHERE user_id = ?", (user_id,)
        ).fetchone()
        is None
    )
    assert (
        conn.execute(
            "SELECT * FROM channels WHERE user_id = ?", (user_id,)
        ).fetchone()
        is None
    )
    assert (
        conn.execute(
            "SELECT * FROM notifications WHERE commute_id = ?", (commute_id,)
        ).fetchone()
        is None
    )


def test_delete_user_leaves_other_users_untouched(conn):
    keep_user = db.create_user(conn)
    keep_commute = _insert_commute(conn, keep_user)
    delete_me = db.create_user(conn)
    _insert_commute(conn, delete_me)

    db.delete_user(conn, delete_me)

    assert conn.execute("SELECT id FROM users WHERE id = ?", (keep_user,)).fetchone() is not None
    assert (
        conn.execute(
            "SELECT id FROM commutes WHERE id = ?", (keep_commute,)
        ).fetchone()
        is not None
    )
