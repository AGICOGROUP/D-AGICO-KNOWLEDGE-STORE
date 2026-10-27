-- Separate read grants survive DingTalk department synchronization.
CREATE TABLE account_access_extensions (
  principal_id text NOT NULL REFERENCES account_users(principal_id),
  organization_id text NOT NULL REFERENCES organizations(id),
  granted_by text NOT NULL REFERENCES account_users(principal_id),
  granted_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(principal_id,organization_id)
);
