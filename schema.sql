CREATE SCHEMA IF NOT EXISTS todos;

CREATE TABLE IF NOT EXISTS todos.lists (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS lists_name_lower ON todos.lists (lower(name));

CREATE TABLE IF NOT EXISTS todos.items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  list_id uuid NOT NULL REFERENCES todos.lists (id) ON DELETE CASCADE,
  body text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);
CREATE INDEX IF NOT EXISTS items_open ON todos.items (list_id, created_at)
  WHERE completed_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS items_open_body ON todos.items (list_id, lower(body))
  WHERE completed_at IS NULL;
