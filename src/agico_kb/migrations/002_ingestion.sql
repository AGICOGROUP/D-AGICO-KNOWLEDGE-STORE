CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
ALTER TABLE versions ADD COLUMN active_generation uuid;
ALTER TABLE versions ADD COLUMN warnings jsonb NOT NULL DEFAULT '[]';
ALTER TABLE versions ADD COLUMN capabilities jsonb NOT NULL DEFAULT '{"text":false,"vector":false,"file":true}';
ALTER TABLE versions ADD COLUMN model_identity text;
ALTER TABLE jobs ADD COLUMN generation uuid;
ALTER TABLE jobs ADD COLUMN attempts integer NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN lease_until timestamptz;
ALTER TABLE jobs ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE jobs ADD COLUMN last_error text;
CREATE INDEX jobs_ready_idx ON jobs(state,next_attempt_at);
CREATE TABLE chunks (
  id uuid PRIMARY KEY, version_id uuid NOT NULL REFERENCES versions(id), generation uuid NOT NULL,
  ordinal integer NOT NULL, text text NOT NULL, locator jsonb NOT NULL,
  keywords tsvector NOT NULL, embedding public.vector, model_identity text,
  UNIQUE(version_id,generation,ordinal)
);
CREATE INDEX chunks_version_generation_idx ON chunks(version_id,generation,ordinal);
CREATE INDEX chunks_keywords_idx ON chunks USING gin(keywords);
CREATE TABLE file_links (
  parent_version_id uuid NOT NULL REFERENCES versions(id),
  child_version_id uuid NOT NULL REFERENCES versions(id),
  label text NOT NULL,
  PRIMARY KEY(parent_version_id,child_version_id),
  CHECK(parent_version_id<>child_version_id)
);
