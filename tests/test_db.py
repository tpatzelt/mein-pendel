from __future__ import annotations

import datetime as dt
import shutil
import sqlite3

import pytest

from pendel import db
from pendel.commute import Commute


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
    assert [row[0] for row in rows] == [
        "0001_initial.sql",
        "0002_commute_stop_names.sql",
        "0003_commute_paused.sql",
    ]


def test_migrate_is_idempotent(conn):
    newly_applied = db.migrate(conn)
    assert newly_applied == []
    assert conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3


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


def test_add_commute_and_list_commutes_round_trip_stop_names(conn):
    user_id = db.create_user(conn)
    commute = Commute(
        origin_stop_id="900100003",
        destination_stop_id="900120003",
        lines=frozenset({"S41"}),
        weekdays=frozenset({0, 1, 2, 3, 4}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name="Alexanderplatz",
        destination_name="Ostkreuz",
    )

    commute_id = db.add_commute(conn, user_id, commute)
    [(row_id, loaded)] = db.list_commutes(conn, user_id)

    assert row_id == commute_id
    assert loaded.origin_name == "Alexanderplatz"
    assert loaded.destination_name == "Ostkreuz"


def test_add_commute_defaults_stop_names_to_empty_string(conn):
    user_id = db.create_user(conn)
    commute = Commute(
        origin_stop_id="900100003",
        destination_stop_id="900120003",
        lines=frozenset({"S41"}),
        weekdays=frozenset({0, 1, 2, 3, 4}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
    )

    db.add_commute(conn, user_id, commute)
    [(_, loaded)] = db.list_commutes(conn, user_id)

    assert loaded.origin_name == ""
    assert loaded.destination_name == ""


def test_migration_0002_upgrades_existing_database_without_data_loss(data_dir):
    old_migrations = data_dir / "old_migrations"
    old_migrations.mkdir()
    shutil.copy(db.MIGRATIONS_DIR / "0001_initial.sql", old_migrations / "0001_initial.sql")

    old_conn = sqlite3.connect(db.db_path())
    old_conn.row_factory = sqlite3.Row
    old_conn.execute("PRAGMA foreign_keys = ON")
    db.migrate(old_conn, migrations_dir=old_migrations)

    user_id = db.create_user(old_conn)
    commute_id = _insert_commute(old_conn, user_id)
    _insert_channel(old_conn, user_id)
    old_conn.close()

    conn = db.connect()
    try:
        applied = {row[0] for row in conn.execute("SELECT filename FROM schema_migrations")}
        assert "0002_commute_stop_names.sql" in applied

        assert conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone() is not None

        commute_row = conn.execute(
            "SELECT origin_stop_id, destination_stop_id, origin_name, destination_name "
            "FROM commutes WHERE id = ?",
            (commute_id,),
        ).fetchone()
        assert commute_row["origin_stop_id"] == "900000100001"
        assert commute_row["destination_stop_id"] == "900000200002"
        assert commute_row["origin_name"] == ""
        assert commute_row["destination_name"] == ""

        assert (
            conn.execute("SELECT target FROM channels WHERE user_id = ?", (user_id,)).fetchone()[
                "target"
            ]
            == "topic-abc"
        )
    finally:
        conn.close()


def test_migration_0003_upgrades_existing_database_without_data_loss(data_dir):
    old_migrations = data_dir / "old_migrations"
    old_migrations.mkdir()
    shutil.copy(db.MIGRATIONS_DIR / "0001_initial.sql", old_migrations / "0001_initial.sql")
    shutil.copy(
        db.MIGRATIONS_DIR / "0002_commute_stop_names.sql",
        old_migrations / "0002_commute_stop_names.sql",
    )

    old_conn = sqlite3.connect(db.db_path())
    old_conn.row_factory = sqlite3.Row
    old_conn.execute("PRAGMA foreign_keys = ON")
    db.migrate(old_conn, migrations_dir=old_migrations)

    user_id = db.create_user(old_conn)
    cur = old_conn.execute(
        "INSERT INTO commutes (user_id, origin_stop_id, destination_stop_id, lines, "
        "weekdays, window_start, window_end, delay_threshold_min, origin_name, "
        "destination_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            "900000100001",
            "900000200002",
            "S41,U5",
            "0,1,2,3,4",
            "07:30",
            "08:00",
            7,
            "Origin Platz",
            "Destination Bahnhof",
        ),
    )
    old_conn.commit()
    commute_id = cur.lastrowid
    _insert_channel(old_conn, user_id)
    old_conn.close()

    conn = db.connect()
    try:
        applied = {row[0] for row in conn.execute("SELECT filename FROM schema_migrations")}
        assert "0003_commute_paused.sql" in applied

        assert conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone() is not None

        channel_row = conn.execute(
            "SELECT kind, target FROM channels WHERE user_id = ?", (user_id,)
        ).fetchone()
        assert channel_row["kind"] == "ntfy"
        assert channel_row["target"] == "topic-abc"

        commute_row = conn.execute(
            "SELECT origin_stop_id, destination_stop_id, lines, weekdays, window_start, "
            "window_end, delay_threshold_min, origin_name, destination_name, paused "
            "FROM commutes WHERE id = ?",
            (commute_id,),
        ).fetchone()
        assert commute_row["origin_stop_id"] == "900000100001"
        assert commute_row["destination_stop_id"] == "900000200002"
        assert commute_row["lines"] == "S41,U5"
        assert commute_row["weekdays"] == "0,1,2,3,4"
        assert commute_row["window_start"] == "07:30"
        assert commute_row["window_end"] == "08:00"
        assert commute_row["delay_threshold_min"] == 7
        assert commute_row["origin_name"] == "Origin Platz"
        assert commute_row["destination_name"] == "Destination Bahnhof"
        assert commute_row["paused"] == 0
    finally:
        conn.close()


def _sample_commute(**overrides) -> Commute:
    fields = dict(
        origin_stop_id="900100003",
        destination_stop_id="900120003",
        lines=frozenset({"S41"}),
        weekdays=frozenset({0, 1, 2, 3, 4}),
        window_start=dt.time(7, 30),
        window_end=dt.time(8, 0),
        origin_name="Alexanderplatz",
        destination_name="Ostkreuz",
    )
    fields.update(overrides)
    return Commute(**fields)


def test_get_commute_returns_owned_commute(conn):
    user_id = db.create_user(conn)
    commute_id = db.add_commute(conn, user_id, _sample_commute())

    loaded = db.get_commute(conn, user_id, commute_id)

    assert loaded is not None
    assert loaded.origin_name == "Alexanderplatz"
    assert loaded.paused is False


def test_get_commute_returns_none_for_unknown_id(conn):
    user_id = db.create_user(conn)
    assert db.get_commute(conn, user_id, 999) is None


def test_get_commute_returns_none_for_another_users_commute(conn):
    owner = db.create_user(conn)
    commute_id = db.add_commute(conn, owner, _sample_commute())
    stranger = db.create_user(conn)

    assert db.get_commute(conn, stranger, commute_id) is None


def test_update_commute_overwrites_fields_and_is_scoped_to_owner(conn):
    owner = db.create_user(conn)
    commute_id = db.add_commute(conn, owner, _sample_commute())
    stranger = db.create_user(conn)

    updated = _sample_commute(
        destination_stop_id="900000900009",
        destination_name="Woltersdorf",
        lines=frozenset({"S41", "U5"}),
        weekdays=frozenset({5, 6}),
        window_start=dt.time(18, 0),
        window_end=dt.time(18, 30),
        delay_threshold_min=10,
        paused=True,
    )

    assert db.update_commute(conn, stranger, commute_id, updated) is False
    unchanged = db.get_commute(conn, owner, commute_id)
    assert unchanged.destination_name == "Ostkreuz"
    assert unchanged.paused is False

    assert db.update_commute(conn, owner, commute_id, updated) is True
    loaded = db.get_commute(conn, owner, commute_id)
    assert loaded.destination_stop_id == "900000900009"
    assert loaded.destination_name == "Woltersdorf"
    assert loaded.lines == frozenset({"S41", "U5"})
    assert loaded.weekdays == frozenset({5, 6})
    assert loaded.window_start == dt.time(18, 0)
    assert loaded.window_end == dt.time(18, 30)
    assert loaded.delay_threshold_min == 10
    assert loaded.paused is True


def test_update_commute_returns_false_for_unknown_id(conn):
    user_id = db.create_user(conn)
    assert db.update_commute(conn, user_id, 999, _sample_commute()) is False


def test_set_paused_toggles_paused_and_is_scoped_to_owner(conn):
    owner = db.create_user(conn)
    commute_id = db.add_commute(conn, owner, _sample_commute())
    stranger = db.create_user(conn)

    assert db.set_paused(conn, stranger, commute_id, True) is False
    assert db.get_commute(conn, owner, commute_id).paused is False

    assert db.set_paused(conn, owner, commute_id, True) is True
    assert db.get_commute(conn, owner, commute_id).paused is True

    assert db.set_paused(conn, owner, commute_id, False) is True
    assert db.get_commute(conn, owner, commute_id).paused is False


def test_list_commutes_exposes_paused_flag(conn):
    user_id = db.create_user(conn)
    commute_id = db.add_commute(conn, user_id, _sample_commute())
    db.set_paused(conn, user_id, commute_id, True)

    [(_, loaded)] = db.list_commutes(conn, user_id)

    assert loaded.paused is True


def test_delete_commute_removes_exactly_one_and_leaves_the_rest(conn):
    owner = db.create_user(conn)
    keep_id = db.add_commute(conn, owner, _sample_commute())
    delete_id = db.add_commute(conn, owner, _sample_commute(origin_name="Elsewhere"))
    other_user = db.create_user(conn)
    other_commute_id = db.add_commute(conn, other_user, _sample_commute())

    assert db.delete_commute(conn, owner, delete_id) is True

    assert db.get_commute(conn, owner, delete_id) is None
    assert db.get_commute(conn, owner, keep_id) is not None
    assert db.get_commute(conn, other_user, other_commute_id) is not None


def test_delete_commute_is_scoped_to_owner(conn):
    owner = db.create_user(conn)
    commute_id = db.add_commute(conn, owner, _sample_commute())
    stranger = db.create_user(conn)

    assert db.delete_commute(conn, stranger, commute_id) is False
    assert db.get_commute(conn, owner, commute_id) is not None


def test_delete_commute_returns_false_for_unknown_id(conn):
    user_id = db.create_user(conn)
    assert db.delete_commute(conn, user_id, 999) is False


def test_list_channels_includes_row_id(conn):
    user_id = db.create_user(conn)
    channel_id = db.add_ntfy_channel(conn, user_id, "pendel-alerts")

    rows = db.list_channels(conn, user_id)

    assert len(rows) == 1
    assert rows[0]["id"] == channel_id


def test_delete_channel_removes_exactly_one_and_leaves_the_rest(conn):
    owner = db.create_user(conn)
    keep_id = db.add_ntfy_channel(conn, owner, "keep-topic")
    delete_id = _insert_channel(conn, owner)
    other_user = db.create_user(conn)
    other_channel_id = db.add_ntfy_channel(conn, other_user, "other-topic")

    assert db.delete_channel(conn, owner, delete_id) is True

    remaining_ids = {row["id"] for row in db.list_channels(conn, owner)}
    assert remaining_ids == {keep_id}
    assert {row["id"] for row in db.list_channels(conn, other_user)} == {other_channel_id}


def test_delete_channel_is_scoped_to_owner(conn):
    owner = db.create_user(conn)
    channel_id = db.add_ntfy_channel(conn, owner, "pendel-alerts")
    stranger = db.create_user(conn)

    assert db.delete_channel(conn, stranger, channel_id) is False
    assert len(db.list_channels(conn, owner)) == 1


def test_delete_channel_returns_false_for_unknown_id(conn):
    user_id = db.create_user(conn)
    assert db.delete_channel(conn, user_id, 999) is False
