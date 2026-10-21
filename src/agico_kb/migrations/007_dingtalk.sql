CREATE TABLE dingtalk_identities (
  corp_id text NOT NULL, user_id text NOT NULL,
  principal_id text NOT NULL UNIQUE REFERENCES account_users(principal_id),
  display_name text NOT NULL, union_id text NOT NULL DEFAULT '',
  department_ids jsonb NOT NULL, organization_ids jsonb NOT NULL,
  enabled boolean NOT NULL DEFAULT true, verified_at timestamptz NOT NULL,
  PRIMARY KEY(corp_id,user_id)
);
CREATE TABLE dingtalk_challenges (
  challenge_hash text PRIMARY KEY, browser_hash text NOT NULL,
  expires_at timestamptz NOT NULL DEFAULT (now()+interval '3 minutes'),
  used_at timestamptz
);
ALTER TABLE account_sessions ADD COLUMN auth_provider text NOT NULL DEFAULT 'local'
  CHECK(auth_provider IN ('local','dingtalk'));
