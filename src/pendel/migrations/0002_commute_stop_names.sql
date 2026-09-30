-- Store origin and destination stop names with each commute (charter G1, G3):
-- the "today" page must show stop names, never raw stop ids.

ALTER TABLE commutes ADD COLUMN origin_name TEXT NOT NULL DEFAULT '';
ALTER TABLE commutes ADD COLUMN destination_name TEXT NOT NULL DEFAULT '';
