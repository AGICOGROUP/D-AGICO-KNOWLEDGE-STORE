CREATE TABLE feedback (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  author_id text NOT NULL REFERENCES principals(id),
  kind text NOT NULL CHECK(kind IN ('problem','request','suggestion')),
  title text NOT NULL,
  description text NOT NULL,
  area text NOT NULL DEFAULT '',
  status text NOT NULL DEFAULT 'new' CHECK(status IN ('new','planned','working','done','declined')),
  reply text NOT NULL DEFAULT '',
  handled_by text REFERENCES principals(id),
  revision integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX feedback_author_created ON feedback(author_id,created_at DESC,id DESC);
CREATE INDEX feedback_status_created ON feedback(status,created_at DESC,id DESC);
CREATE TABLE feedback_events (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  feedback_id bigint NOT NULL REFERENCES feedback(id),
  actor_id text NOT NULL REFERENCES principals(id),
  action text NOT NULL,
  detail jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
