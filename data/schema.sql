CREATE TABLE IF NOT EXISTS goals (
  slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  total_tasks INTEGER DEFAULT 0,
  completed_tasks INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  goal_slug TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  estimated_hours REAL,
  depends_on TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  last_reminded_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (goal_slug) REFERENCES goals(slug)
);

CREATE INDEX IF NOT EXISTS idx_tasks_goal ON tasks(goal_slug);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);