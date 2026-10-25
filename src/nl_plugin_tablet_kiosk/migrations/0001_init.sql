-- Runs in this plugin's own schema (search_path), once, in a transaction.
-- Platform ids are stored without foreign keys (AGENTS.md rule 3); forget_deleted() cleans up.

-- Connection to the tablet, one version per save.
CREATE TABLE settings_versions (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  doc         JSONB NOT NULL,
  created_by  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- What the tablet does while a record of an experiment runs, one version per save.
CREATE TABLE plan_versions (
  id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  experiment_id  BIGINT NOT NULL,
  version_no     INTEGER NOT NULL,
  doc            JSONB NOT NULL,
  created_by     TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (experiment_id, version_no)
);

-- The plan a record used: taken when the plugin first saw the record running
-- (inferred = the record had started before the plugin saw it).
CREATE TABLE runs (
  run_id           TEXT PRIMARY KEY,
  experiment_id    BIGINT,
  plan_version_id  BIGINT REFERENCES plan_versions (id) ON DELETE SET NULL,
  plan             JSONB,
  inferred         BOOLEAN NOT NULL DEFAULT false,
  seen_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Stimulus media library; files are $NL_PLUGIN_DATA_DIR/media/<name>, name = <sha256>.<ext>.
CREATE TABLE media (
  name           TEXT PRIMARY KEY,
  original_name  TEXT NOT NULL,
  kind           TEXT NOT NULL CHECK (kind IN ('video', 'image', 'audio')),
  bytes          BIGINT NOT NULL,
  uploaded_by    TEXT,
  uploaded_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One capture session of the tablet (video + audio tracks + touch); files are
-- $NL_PLUGIN_DATA_DIR/captures/<session_id>/.
CREATE TABLE captures (
  session_id     TEXT PRIMARY KEY,
  kind           TEXT NOT NULL CHECK (kind IN ('run', 'manual', 'other')),
  run_id         TEXT,
  experiment_id  BIGINT,
  part           INTEGER NOT NULL DEFAULT 1,
  plan_version_id BIGINT,
  request        JSONB NOT NULL DEFAULT '{}',
  requested_by   TEXT,
  requested_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at     TIMESTAMPTZ,
  ended_at       TIMESTAMPTZ,
  stop_reason    TEXT,
  complete       BOOLEAN NOT NULL DEFAULT false,   -- session.json fetched: every file is here
  meta           JSONB,                            -- the tablet's session.json
  summary        JSONB NOT NULL DEFAULT '{}'       -- touch counts and other figures computed on fetch
);
CREATE INDEX captures_run ON captures (run_id) WHERE run_id IS NOT NULL;
CREATE INDEX captures_requested ON captures (requested_at DESC);

CREATE TABLE capture_files (
  session_id  TEXT NOT NULL REFERENCES captures (session_id) ON DELETE CASCADE,
  name        TEXT NOT NULL,
  bytes       BIGINT NOT NULL,
  stored_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  info        JSONB NOT NULL DEFAULT '{}',
  PRIMARY KEY (session_id, name)
);

-- What happened: the tablet's own events (source 'tablet', with its sequence
-- number) and the plugin's decisions (source 'plugin').
CREATE TABLE events (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  at          TIMESTAMPTZ NOT NULL,
  source      TEXT NOT NULL CHECK (source IN ('tablet', 'plugin')),
  name        TEXT NOT NULL,
  session_id  TEXT,
  run_id      TEXT,
  actor       TEXT,
  data        JSONB NOT NULL DEFAULT '{}',
  tablet_instance TEXT,
  tablet_seq  BIGINT
);
CREATE INDEX events_at ON events (at DESC);
CREATE INDEX events_run ON events (run_id, at) WHERE run_id IS NOT NULL;
CREATE INDEX events_session ON events (session_id, at) WHERE session_id IS NOT NULL;
CREATE UNIQUE INDEX events_tablet_seq ON events (tablet_instance, tablet_seq) WHERE tablet_seq IS NOT NULL;

-- Health of the tablet, one compact row per sample (every 15 s, kept 31 days).
CREATE TABLE samples (
  at    TIMESTAMPTZ NOT NULL,
  data  JSONB NOT NULL
);
CREATE INDEX samples_at ON samples (at);
