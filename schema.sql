CREATE SCHEMA IF NOT EXISTS todos;

CREATE TABLE IF NOT EXISTS todos.lists (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  comment text
);
CREATE UNIQUE INDEX IF NOT EXISTS lists_name_lower ON todos.lists (lower(name));

CREATE TABLE IF NOT EXISTS todos.items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  list_id uuid NOT NULL REFERENCES todos.lists (id) ON DELETE CASCADE,
  body text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz,
  comment text
);
CREATE INDEX IF NOT EXISTS items_open ON todos.items (list_id, created_at)
  WHERE completed_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS items_open_body ON todos.items (list_id, lower(body))
  WHERE completed_at IS NULL;

-- Databases created before the comment columns.
ALTER TABLE todos.lists ADD COLUMN IF NOT EXISTS comment text;
ALTER TABLE todos.items ADD COLUMN IF NOT EXISTS comment text;
