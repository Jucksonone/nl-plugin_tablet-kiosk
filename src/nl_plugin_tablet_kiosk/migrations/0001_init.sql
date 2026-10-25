-- Runs in this plugin's own schema (search_path), once, in a transaction.
-- Never reference platform tables with a foreign key: store their ids (AGENTS.md rule 3).
CREATE TABLE notes (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  experiment_id BIGINT NOT NULL,
  text          TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 2000),
  author        TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX notes_experiment ON notes (experiment_id, id DESC);
