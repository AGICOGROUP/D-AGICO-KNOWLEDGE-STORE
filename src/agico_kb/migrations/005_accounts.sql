CREATE TABLE account_settings (id integer PRIMARY KEY CHECK(id=1), enabled boolean NOT NULL DEFAULT false);
INSERT INTO account_settings(id) VALUES (1);
CREATE TABLE account_users (
  principal_id text PRIMARY KEY REFERENCES principals(id),
  email text NOT NULL UNIQUE CHECK(email=lower(email)),
  account_role text NOT NULL CHECK(account_role IN ('superadmin','manager','user')),
  status text NOT NULL CHECK(status IN ('invited','active','disabled')),
  password_hash text,
  must_change_password boolean NOT NULL DEFAULT false,
  created_by text REFERENCES principals(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE account_sessions (
  session_hash text PRIMARY KEY, principal_id text NOT NULL REFERENCES account_users(principal_id),
  csrf_token text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL DEFAULT (now()+interval '8 hours')
);
CREATE INDEX account_session_owner ON account_sessions(principal_id);
CREATE TABLE account_invitations (
  id uuid PRIMARY KEY, principal_id text NOT NULL REFERENCES account_users(principal_id),
  secret_hash text NOT NULL UNIQUE, purpose text NOT NULL CHECK(purpose IN ('activate','reset')),
  created_by text NOT NULL REFERENCES principals(id), created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL DEFAULT (now()+interval '7 days'),
  used_at timestamptz, revoked_at timestamptz
);
CREATE INDEX account_invitation_owner ON account_invitations(principal_id);
CREATE TABLE account_audit (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, actor_id text REFERENCES principals(id),
  target_id text REFERENCES principals(id), action text NOT NULL,
  detail jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE account_login_limits (
  key_hash text PRIMARY KEY, failures integer NOT NULL DEFAULT 0,
  window_start timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE oauth_clients (
  client_id text PRIMARY KEY, name text NOT NULL, redirect_uris jsonb NOT NULL,
  metadata jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE oauth_pending (
  request_hash text PRIMARY KEY, client_id text NOT NULL REFERENCES oauth_clients(client_id),
  parameters jsonb NOT NULL, expires_at timestamptz NOT NULL DEFAULT (now()+interval '10 minutes'),
  used_at timestamptz
);
CREATE TABLE oauth_codes (
  code_hash text PRIMARY KEY, principal_id text NOT NULL REFERENCES account_users(principal_id),
  client_id text NOT NULL REFERENCES oauth_clients(client_id), redirect_uri text NOT NULL,
  resource text NOT NULL, challenge text NOT NULL, expires_at timestamptz NOT NULL,
  redirect_explicit boolean NOT NULL DEFAULT true,
  used_at timestamptz
);
CREATE TABLE oauth_grants (
  id uuid PRIMARY KEY, principal_id text NOT NULL REFERENCES account_users(principal_id),
  client_id text NOT NULL REFERENCES oauth_clients(client_id), resource text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), revoked boolean NOT NULL DEFAULT false
);
CREATE TABLE oauth_credentials (
  secret_hash text PRIMARY KEY, grant_id uuid NOT NULL REFERENCES oauth_grants(id),
  kind text NOT NULL CHECK(kind IN ('access','refresh')), expires_at timestamptz NOT NULL,
  used_at timestamptz
);
CREATE INDEX oauth_grant_owner ON oauth_grants(principal_id);
