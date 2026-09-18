ALTER TABLE principals ADD COLUMN is_admin boolean NOT NULL DEFAULT false;
CREATE TABLE managed_access (
    id uuid PRIMARY KEY,
    principal_id text NOT NULL UNIQUE REFERENCES principals(id),
    token_hash text NOT NULL UNIQUE REFERENCES access_tokens(token_hash),
    created_by text NOT NULL REFERENCES principals(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_by text REFERENCES principals(id),
    revoked_at timestamptz
);
CREATE INDEX managed_access_created ON managed_access(created_at DESC, id DESC);
