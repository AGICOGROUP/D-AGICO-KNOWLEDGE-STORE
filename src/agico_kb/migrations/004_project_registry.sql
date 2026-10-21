CREATE SEQUENCE IF NOT EXISTS opportunity_code_seq;

CREATE TABLE projects (
  id uuid PRIMARY KEY,
  organization_id text NOT NULL REFERENCES organizations(id),
  code text NOT NULL,
  official_code text,
  status text NOT NULL CHECK (status IN ('opportunity', 'won')),
  name text NOT NULL,
  aliases jsonb NOT NULL DEFAULT '[]',
  evidence jsonb NOT NULL DEFAULT '{}',
  visibility text NOT NULL CHECK (visibility IN ('department', 'private')),
  created_by text NOT NULL REFERENCES principals(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (organization_id, code)
);
CREATE UNIQUE INDEX projects_official_code_unique ON projects(organization_id, official_code)
  WHERE official_code IS NOT NULL;
CREATE INDEX projects_organization_idx ON projects(organization_id, created_at DESC);
ALTER TABLE documents ADD COLUMN project_id uuid REFERENCES projects(id);
CREATE INDEX documents_project_id_idx ON documents(project_id);
