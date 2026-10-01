from __future__ import annotations

import dataclasses
import datetime as dt
import sqlite3

import pytest

from pendel import db, tracker
from pendel.engine import Verdict
from pendel.notify import FakeChannel

NOW = dt.datetime(2026, 1, 12, 7, 45, tzinfo=dt.timezone.utc)

AFFECTED = Verdict(
    affected=True,
    kinds=["cancellation"],
    reason_de="S41 fällt aus.",
    reason_en="S41 is cancelled.",
    disruption_key="cancellation:S41:trip-1",
)

AFFECTED_OTHER = Verdict(
    affected=True,
    kinds=["delay"],
    reason_de="S41 hat 12 Minuten Verspätung.",
    reason_en="S41 is delayed by 12 minutes.",
    disruption_key="delay:S41:trip-2",
)

UNAFFECTED = Verdict(
    affected=False,
    kinds=[],
    reason_de="Keine Störung auf dieser Verbindung.",
    reason_en="No disruption on this route.",
    disruption_key="",
)

# A verdict whose reason text does not itself mention the line, so a test
# asserting the line appears in the message actually exercises
# `verdict.line` rather than incidentally matching the reason string.
# `planned` is a summer (CEST, UTC+2) instant, pinning down the DST offset
# used when rendering the Europe/Berlin HH:MM.
AFFECTED_WITH_LINE_AND_TIME = dataclasses.replace(
    AFFECTED,
    line="U8",
    reason_de="Zug fällt aus.",
    reason_en="Train is cancelled.",
    planned=dt.datetime(2026, 7, 15, 10, 30, tzinfo=dt.timezone.utc),
)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PENDEL_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def conn(data_dir):
    connection = db.connect()
    yield connection
    connection.close()


def _commute_id(
    conn: sqlite3.Connection,
    user_id: str,
    *,
    origin_name: str = "",
    destination_name: str = "",
) -> int:
    cur = conn.execute(
        "INSERT INTO commutes (user_id, origin_stop_id, destination_stop_id, "
        "lines, weekdays, window_start, window_end, origin_name, destination_name) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            "900000100001",
            "900000200002",
            "S41",
            "0,1,2,3,4",
            "07:30",
            "08:00",
            origin_name,
            destination_name,
        ),
    )
    conn.commit()
    return cur.lastrowid


def _add_channel(
    conn: sqlite3.Connection,
    user_id: str,
    kind: str,
    *,
    target: str | None = None,
    link_token: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO channels (user_id, kind, target, link_token) VALUES (?, ?, ?, ?)",
        (user_id, kind, target, link_token),
    )
    conn.commit()


def _notification_row(conn: sqlite3.Connection, commute_id: int, disruption_key: str):
    return conn.execute(
        "SELECT state, first_notified_at, resolved_at FROM notifications "
        "WHERE commute_id = ? AND disruption_key = ?",
        (commute_id, disruption_key),
    ).fetchone()


@pytest.fixture
def commute(conn):
    """A commute for a fresh user with one linked ntfy channel."""
    user_id = db.create_user(conn)
    commute_id = _commute_id(conn, user_id)
    _add_channel(conn, user_id, "ntfy", target="topic-abc")
    return user_id, commute_id


def test_new_disruption_sends_one_message_and_marks_active(conn, commute):
    _user_id, commute_id = commute
    fake = FakeChannel()

    tracker.process(conn, commute_id, AFFECTED, {"ntfy": fake}, NOW)

    assert len(fake.sent) == 1
    target, text = fake.sent[0]
    assert target == "topic-abc"
    assert "S41 fällt aus." in text
    assert "S41 is cancelled." in text

    row = _notification_row(conn, commute_id, AFFECTED.disruption_key)
    assert row["state"] == "active"
    assert row["first_notified_at"] == NOW.isoformat()
    assert row["resolved_at"] is None


def test_repeated_identical_verdict_sends_exactly_one_message(conn, commute):
    _user_id, commute_id = commute
    fake = FakeChannel()

    tracker.process(conn, commute_id, AFFECTED, {"ntfy": fake}, NOW)
    tracker.process(conn, commute_id, AFFECTED, {"ntfy": fake}, NOW)

    assert len(fake.sent) == 1


def test_unaffected_verdict_sends_nothing(conn, commute):
    _user_id, commute_id = commute
    fake = FakeChannel()

    tracker.process(conn, commute_id, UNAFFECTED, {"ntfy": fake}, NOW)

    assert fake.sent == []
    assert conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0


def test_cleared_disruption_sends_exactly_one_resolved_message(conn, commute):
    _user_id, commute_id = commute
    fake = FakeChannel()

    tracker.process(conn, commute_id, AFFECTED, {"ntfy": fake}, NOW)
    resolved_at = NOW + dt.timedelta(minutes=10)
    tracker.process(conn, commute_id, UNAFFECTED, {"ntfy": fake}, resolved_at)

    assert len(fake.sent) == 2
    target, text = fake.sent[1]
    assert target == "topic-abc"
    assert "S41" in text
    assert "behoben" in text  # AFFECTED's key is "cancellation:...": "Ausfall auf S41 behoben."
    assert "resolved" in text  # "Cancellation on S41 resolved."

    row = _notification_row(conn, commute_id, AFFECTED.disruption_key)
    assert row["state"] == "resolved"
    assert row["resolved_at"] == resolved_at.isoformat()

    # already resolved: a further unaffected tick sends nothing more
    tracker.process(conn, commute_id, UNAFFECTED, {"ntfy": fake}, resolved_at + dt.timedelta(minutes=5))
    assert len(fake.sent) == 2


def test_reactivated_disruption_sends_again_and_clears_resolved_at(conn, commute):
    _user_id, commute_id = commute
    fake = FakeChannel()

    tracker.process(conn, commute_id, AFFECTED, {"ntfy": fake}, NOW)
    resolved_at = NOW + dt.timedelta(minutes=10)
    tracker.process(conn, commute_id, UNAFFECTED, {"ntfy": fake}, resolved_at)
    reactivated_at = resolved_at + dt.timedelta(minutes=10)
    tracker.process(conn, commute_id, AFFECTED, {"ntfy": fake}, reactivated_at)

    assert len(fake.sent) == 3
    row = _notification_row(conn, commute_id, AFFECTED.disruption_key)
    assert row["state"] == "active"
    assert row["resolved_at"] is None
    assert row["first_notified_at"] == reactivated_at.isoformat()


def test_replaced_disruption_resolves_old_key_and_activates_new_key(conn, commute):
    _user_id, commute_id = commute
    fake = FakeChannel()

    tracker.process(conn, commute_id, AFFECTED, {"ntfy": fake}, NOW)
    later = NOW + dt.timedelta(minutes=10)
    tracker.process(conn, commute_id, AFFECTED_OTHER, {"ntfy": fake}, later)

    assert len(fake.sent) == 3  # activate S41 cancellation, resolve it, activate S41 delay

    old_row = _notification_row(conn, commute_id, AFFECTED.disruption_key)
    assert old_row["state"] == "resolved"
    assert old_row["resolved_at"] == later.isoformat()

    new_row = _notification_row(conn, commute_id, AFFECTED_OTHER.disruption_key)
    assert new_row["state"] == "active"
    assert new_row["resolved_at"] is None


def test_unlinked_channel_is_never_sent_to(conn):
    user_id = db.create_user(conn)
    commute_id = _commute_id(conn, user_id)
    _add_channel(conn, user_id, "telegram", target=None, link_token="pending-token")
    _add_channel(conn, user_id, "ntfy", target="topic-xyz")
    fake = FakeChannel()

    tracker.process(conn, commute_id, AFFECTED, {"telegram": fake, "ntfy": fake}, NOW)

    assert len(fake.sent) == 1
    assert fake.sent[0][0] == "topic-xyz"


def test_no_channels_still_records_state_but_sends_nothing(conn):
    user_id = db.create_user(conn)
    commute_id = _commute_id(conn, user_id)
    fake = FakeChannel()

    tracker.process(conn, commute_id, AFFECTED, {}, NOW)

    assert fake.sent == []
    row = _notification_row(conn, commute_id, AFFECTED.disruption_key)
    assert row["state"] == "active"


def test_unknown_commute_id_raises(conn):
    with pytest.raises(ValueError):
        tracker.process(conn, 999, AFFECTED, {}, NOW)


def test_disruption_message_names_commute_line_time_reason_and_today_link(conn):
    user_id = db.create_user(conn)
    commute_id = _commute_id(
        conn, user_id, origin_name="Alexanderplatz", destination_name="Potsdam Hbf"
    )
    _add_channel(conn, user_id, "ntfy", target="topic-abc")
    fake = FakeChannel()

    tracker.process(conn, commute_id, AFFECTED_WITH_LINE_AND_TIME, {"ntfy": fake}, NOW)

    assert len(fake.sent) == 1
    _target, text = fake.sent[0]
    assert "Alexanderplatz" in text
    assert "Potsdam Hbf" in text
    assert "U8" in text
    # 2026-07-15 10:30 UTC is 12:30 in Europe/Berlin (CEST, DST in effect).
    assert "12:30" in text
    assert "Zug fällt aus." in text
    assert "Train is cancelled." in text
    assert text.rstrip().endswith("/today")


def test_disruption_message_uses_generic_commute_name_when_names_missing(conn, commute):
    _user_id, commute_id = commute
    fake = FakeChannel()

    tracker.process(conn, commute_id, AFFECTED_WITH_LINE_AND_TIME, {"ntfy": fake}, NOW)

    _target, text = fake.sent[0]
    assert "Deine Verbindung" in text
    assert "Your commute" in text


def test_disruption_message_today_url_has_no_accidental_double_slash(conn, commute):
    _user_id, commute_id = commute
    fake = FakeChannel()

    tracker.process(conn, commute_id, AFFECTED, {"ntfy": fake}, NOW)

    _target, text = fake.sent[0]
    assert "//today" not in text


def test_format_disruption_message_is_pure_de_block_before_en_block_and_ends_with_url():
    text = tracker.format_disruption_message(
        "Alexanderplatz → Potsdam Hbf",
        AFFECTED_WITH_LINE_AND_TIME,
        "https://example.invalid/today",
    )

    assert text.endswith("https://example.invalid/today")
    assert text.index("Zug fällt aus.") < text.index("Train is cancelled.")


def test_format_disruption_message_missing_planned_renders_question_mark():
    verdict = dataclasses.replace(AFFECTED, line="U8", planned=None)

    text = tracker.format_disruption_message("A → B", verdict, "/today")

    assert "?" in text


@pytest.mark.parametrize(
    "kind,expected_de,expected_en",
    [
        ("cancellation", "Ausfall auf S41 behoben.", "Cancellation on S41 resolved."),
        ("delay", "Verspätung auf S41 behoben.", "Delay on S41 resolved."),
        ("replacement_service", "Ersatzverkehr auf S41 behoben.", "Replacement service on S41 resolved."),
        ("construction", "Bauarbeiten auf S41 behoben.", "Construction on S41 resolved."),
        ("warning", "Störung auf S41 behoben.", "Disruption on S41 resolved."),
        ("some_future_kind", "Störung auf S41 behoben.", "Disruption on S41 resolved."),
    ],
)
def test_resolved_message_names_commute_kind_phrase_and_line(conn, kind, expected_de, expected_en):
    user_id = db.create_user(conn)
    commute_id = _commute_id(
        conn, user_id, origin_name="Alexanderplatz", destination_name="Potsdam Hbf"
    )
    _add_channel(conn, user_id, "ntfy", target="topic-abc")
    fake = FakeChannel()
    verdict = dataclasses.replace(AFFECTED, disruption_key=f"{kind}:S41:trip-x")

    tracker.process(conn, commute_id, verdict, {"ntfy": fake}, NOW)
    tracker.process(conn, commute_id, UNAFFECTED, {"ntfy": fake}, NOW + dt.timedelta(minutes=5))

    assert len(fake.sent) == 2  # exactly one resolved message for the cleared key
    _target, text = fake.sent[1]
    assert "Alexanderplatz" in text
    assert "Potsdam Hbf" in text
    assert expected_de in text
    assert expected_en in text
    assert text.rstrip().endswith("/today")


def test_format_resolved_message_uses_generic_commute_name_when_names_missing():
    text = tracker.format_resolved_message("", "cancellation:S41:trip-1", "/today")

    assert "Deine Verbindung" in text
    assert "Your commute" in text
    assert "S41" in text
