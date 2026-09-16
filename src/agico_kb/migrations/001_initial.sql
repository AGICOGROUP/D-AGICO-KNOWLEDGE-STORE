CREATE TABLE organizations (id text PRIMARY KEY, name text NOT NULL);
CREATE TABLE categories (id text PRIMARY KEY, name text NOT NULL);
CREATE TABLE principals (id text PRIMARY KEY, name text NOT NULL, active boolean NOT NULL DEFAULT true);
CREATE TABLE memberships (
  principal_id text REFERENCES principals(id), organization_id text REFERENCES organizations(id),
  role text NOT NULL CHECK(role IN ('member','publisher')),
  PRIMARY KEY(principal_id, organization_id)
);
CREATE TABLE access_tokens (
  token_hash text PRIMARY KEY, principal_id text NOT NULL REFERENCES principals(id),
  revoked boolean NOT NULL DEFAULT false, expires_at timestamptz
);
CREATE TABLE documents (
  id uuid PRIMARY KEY, organization_id text NOT NULL REFERENCES organizations(id),
  category_id text NOT NULL REFERENCES categories(id), title text NOT NULL,
  created_by text NOT NULL REFERENCES principals(id), visibility text NOT NULL CHECK(visibility IN ('department','private')),
  product text, model text, project text, business_date date,
  active_version_id uuid, revision integer NOT NULL DEFAULT 0 CHECK(revision >= 0),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE document_grants (
  document_id uuid REFERENCES documents(id), principal_id text REFERENCES principals(id),
  PRIMARY KEY(document_id,principal_id)
);
CREATE TABLE uploads (
  id uuid PRIMARY KEY, owner_id text NOT NULL REFERENCES principals(id), filename text NOT NULL,
  expected_size bigint NOT NULL CHECK(expected_size > 0), expected_sha256 text,
  size bigint, sha256 text, blob_key text,
  state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','complete','submitted')),
  idempotency_key text NOT NULL, request_hash text NOT NULL, submit_hash text,
  created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL DEFAULT (now()+interval '24 hours'),
  UNIQUE(owner_id,idempotency_key)
);
CREATE TABLE versions (
  id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES documents(id),
  upload_id uuid NOT NULL UNIQUE REFERENCES uploads(id), created_by text NOT NULL REFERENCES principals(id),
  source_version text, base_revision integer NOT NULL CHECK(base_revision>=0),
  state text NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','published','superseded','withdrawn')),
  processing_status text NOT NULL DEFAULT 'queued' CHECK(processing_status IN ('queued','processing','ready','partial','failed','stored_only')),
  publication_mode text CHECK(publication_mode IN ('content','file_only')),
  created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz,
  UNIQUE(document_id,id)
);
ALTER TABLE documents ADD CONSTRAINT active_version_belongs_to_document
  FOREIGN KEY(id,active_version_id) REFERENCES versions(document_id,id);
CREATE UNIQUE INDEX one_published_version ON versions(document_id) WHERE state='published';
CREATE TABLE jobs (
  id uuid PRIMARY KEY, version_id uuid NOT NULL UNIQUE REFERENCES versions(id),
  state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','complete','failed','cancelled')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE audit_events (
  id bigserial PRIMARY KEY, actor_id text NOT NULL REFERENCES principals(id),
  document_id uuid REFERENCES documents(id), version_id uuid REFERENCES versions(id),
  action text NOT NULL, details jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX documents_org_idx ON documents(organization_id);
CREATE INDEX versions_doc_idx ON versions(document_id);
CREATE INDEX audit_doc_idx ON audit_events(document_id);
