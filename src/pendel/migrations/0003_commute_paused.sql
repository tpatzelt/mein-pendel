-- Paused commutes (charter G2): a visitor can pause a commute so the
-- scheduler never checks it, without deleting it.

ALTER TABLE commutes ADD COLUMN paused INTEGER NOT NULL DEFAULT 0;
