-- Initial schema: users, commutes, notification channels, notification state.
-- Privacy (G5): no email or name columns anywhere.

CREATE TABLE users (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE commutes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    origin_stop_id TEXT NOT NULL,
    destination_stop_id TEXT NOT NULL,
    lines TEXT NOT NULL,
    weekdays TEXT NOT NULL,
    window_start TEXT NOT NULL,
    window_end TEXT NOT NULL,
    delay_threshold_min INTEGER NOT NULL DEFAULT 5,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_commutes_user_id ON commutes(user_id);

CREATE TABLE channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('telegram', 'ntfy')),
    target TEXT,
    link_token TEXT,
    linked_at TEXT
);

CREATE INDEX idx_channels_user_id ON channels(user_id);

CREATE TABLE notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    commute_id INTEGER NOT NULL REFERENCES commutes(id) ON DELETE CASCADE,
    disruption_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'resolved')),
    first_notified_at TEXT NOT NULL,
    resolved_at TEXT,
    UNIQUE (commute_id, disruption_key)
);

CREATE INDEX idx_notifications_commute_id ON notifications(commute_id);
