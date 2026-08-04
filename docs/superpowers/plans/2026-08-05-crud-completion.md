# CRUD Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the missing CRUD surface (`list` / `show` / `update` / `delete` / `restore`) for goals and tasks to the CLI, plus minimal read-only dashboard updates, with soft-delete (`status='archived'`) so the operation is reversible and `goals/index.md` stays in sync via `_autosync_index_md()`.

**Architecture:** Reuse the existing status field for soft delete (`archived` becomes a 4th goal status / 5th task status). New DB primitives (`archive_goal/task`, `restore_goal/task`) wrap existing `update_*_status`. `sync_md.py` filters archived out of rendering and `by_status`. `scheduler.py:list_eligible_tasks` skips archived tasks. CLI gains 9 new subcommands; mutating ones call `_autosync_index_md()`. Dashboard gains index-page filters, a goal-detail archived banner, and a new `/task/<id>` route. No schema migration, no new dependencies.

**Tech Stack:** Python 3 stdlib (`argparse`, `sqlite3`, `pathlib`, `json`); pytest with subprocess isolation via Item 5's `TODO_GOALS_DIR`; Flask test client (existing).

## Global Constraints

These constraints bind every task. Treat them as load-bearing requirements.

- **Soft delete only.** `goal delete` / `task delete` set `status='archived'`. Hard row deletion is out of scope; `restore` always returns to `active` (goal) / `pending` (task).
- **Update scope = status only.** `goal update` accepts `--status <active|paused|completed>` only. `--status archived` is rejected (exit 2) with a hint to use `goal delete`. No `--name` / `--description` / `--title` / `--hours` / `--depends-on` in v1.
- **List defaults hide archived.** `goal list` / `task list` exclude archived by default. `--all` shows everything. `--status <key>` filters to one status (validates against the known set).
- **Restore is strict.** `goal restore` / `task restore` on a non-archived row exit 2. Restore does not accept a `--to` flag in v1.
- **Sync triggers.** `_autosync_index_md()` fires ONLY on actual change. `archive_*` returns bool (True = changed); `restore_*` raises on no-op so the caller can skip sync.
- **Dashboard is read-only.** No POST routes. No update/delete buttons in HTML. Archived filter and banner are visual only.
- **CLI conventions** (from Items 1/5): `--json` flag, exit 0/1/2, errors to stderr, JSON via `to_json`, `--help` from argparse.
- **Test isolation:** Subprocess tests use Item 5's `TODO_GOALS_DIR` env override. Pure DB tests use `monkeypatch.setattr(db, "DB_PATH", str(db_path))`. Dashboard tests use the Flask test client.
- **Full-width colon `：` (U+FF1A)** in user-facing strings, matching Item 5 sync-md convention.
- **Status labels for tasks** (current): `pending` / `in_progress` / `done` / `skipped` / **new** `archived`. **For goals**: `active` / `paused` / `completed` / **new** `archived`.
- **No new Python dependencies.** Stdlib only.
- **No DB schema migration.** The `status` column is TEXT; `'archived'` is just another string value.

---

### Task 1: db.py — `archive_goal` / `archive_task` / `restore_goal` / `restore_task`

**Files:**
- Modify: `scripts/db.py` (append 4 functions after `update_task_status`, ~line 191)
- Modify: `tests/test_db.py` (append `TestArchiveRestore` class)

**Interfaces:**
- Consumes: existing `update_goal_status(slug, status)`, `update_task_status(id, status)`, `get_goal(slug)`, `get_task(id)`, `now_iso()`.
- Produces:
  - `archive_goal(slug: str) -> bool` — sets `status='archived'`, returns True if changed, False if already archived.
  - `archive_task(task_id: str) -> bool` — same semantics.
  - `restore_goal(slug: str) -> None` — sets archived → active. Raises `ValueError("goal '<slug>' is not archived")` if status != 'archived'. Raises `ValueError("goal '<slug>' does not exist")` if get_goal returns None.
  - `restore_task(task_id: str) -> None` — same semantics; archived → pending.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_db.py`:

```python
class TestArchiveRestore:
    def _setup(self, tmp_path, monkeypatch):
        db_path = tmp_path / "todos.db"
        monkeypatch.setattr(db, "DB_PATH", str(db_path))
        db._init_schema()
        return db_path

    def _make_goal(self, slug="g", name="G", status="active"):
        db.create_goal(slug, name, "")
        db.update_goal_status(slug, status)
        return db.get_goal(slug)

    def _make_task(self, task_id="g-T001", slug="g", status="pending"):
        db.create_task(task_id, slug, 1, "T", 1.0, "[]", status)
        return db.get_task(task_id)

    # --- archive_goal ---

    def test_archive_goal_sets_archived(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        self._make_goal()
        changed = db.archive_goal("g")
        assert changed is True
        assert db.get_goal("g")["status"] == "archived"

    def test_archive_goal_idempotent_returns_false(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        self._make_goal(status="active")
        assert db.archive_goal("g") is True
        # second call: already archived, no-op
        assert db.archive_goal("g") is False

    def test_archive_goal_missing_raises(self, tmp_path, monkeypatch):
        import pytest
        self._setup(tmp_path, monkeypatch)
        with pytest.raises(ValueError):
            db.archive_goal("does-not-exist")

    # --- restore_goal ---

    def test_restore_goal_archived_to_active(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        self._make_goal(status="archived")
        db.restore_goal("g")
        assert db.get_goal("g")["status"] == "active"

    def test_restore_goal_non_archived_raises(self, tmp_path, monkeypatch):
        import pytest
        self._setup(tmp_path, monkeypatch)
        self._make_goal(status="active")
        with pytest.raises(ValueError, match="not archived"):
            db.restore_goal("g")

    def test_restore_goal_missing_raises(self, tmp_path, monkeypatch):
        import pytest
        self._setup(tmp_path, monkeypatch)
        with pytest.raises(ValueError, match="does not exist"):
            db.restore_goal("nope")

    # --- archive_task / restore_task ---

    def test_archive_task_sets_archived(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        self._make_goal()
        self._make_task()
        assert db.archive_task("g-T001") is True
        assert db.get_task("g-T001")["status"] == "archived"

    def test_archive_task_idempotent(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        self._make_goal()
        self._make_task()
        db.archive_task("g-T001")
        assert db.archive_task("g-T001") is False

    def test_archive_task_missing_raises(self, tmp_path, monkeypatch):
        import pytest
        self._setup(tmp_path, monkeypatch)
        with pytest.raises(ValueError):
            db.archive_task("does-not-exist")

    def test_restore_task_archived_to_pending(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        self._make_goal()
        self._make_task(status="archived")
        db.restore_task("g-T001")
        assert db.get_task("g-T001")["status"] == "pending"

    def test_restore_task_non_archived_raises(self, tmp_path, monkeypatch):
        import pytest
        self._setup(tmp_path, monkeypatch)
        self._make_goal()
        self._make_task(status="done")
        with pytest.raises(ValueError, match="not archived"):
            db.restore_task("g-T001")
```

(Adapt the import block / fixture setup at the top of `tests/test_db.py` to ensure `db`, `pytest`, `monkeypatch` are imported. The file already uses these per Item 5 evidence; if not, add `import pytest` and `from _pytest.monkeypatch import MonkeyPatch` style usage as elsewhere.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_db.py::TestArchiveRestore -v`

Expected: all FAIL with `AttributeError: module 'db' has no attribute 'archive_goal'` (and similar for restore_goal/task variants).

- [ ] **Step 3: Implement the 4 db.py functions**

Append to `scripts/db.py` immediately after `update_task_status`:

```python
def archive_goal(slug: str) -> bool:
    """Soft-delete a goal by setting status='archived'.

    Returns True if the status changed, False if already archived (no-op).
    Raises ValueError if the goal does not exist.
    """
    current = get_goal(slug)
    if current is None:
        raise ValueError(f"goal '{slug}' does not exist")
    if current["status"] == "archived":
        return False
    update_goal_status(slug, "archived")
    return True


def archive_task(task_id: str) -> bool:
    """Soft-delete a task by setting status='archived'.

    Returns True if the status changed, False if already archived (no-op).
    Raises ValueError if the task does not exist.
    """
    current = get_task(task_id)
    if current is None:
        raise ValueError(f"task '{task_id}' does not exist")
    if current["status"] == "archived":
        return False
    update_task_status(task_id, "archived")
    return True


def restore_goal(slug: str) -> None:
    """Restore an archived goal back to 'active'.

    Raises ValueError if the goal does not exist or is not currently archived.
    """
    current = get_goal(slug)
    if current is None:
        raise ValueError(f"goal '{slug}' does not exist")
    if current["status"] != "archived":
        raise ValueError(f"goal '{slug}' is not archived (status='{current['status']}')")
    update_goal_status(slug, "active")


def restore_task(task_id: str) -> None:
    """Restore an archived task back to 'pending'.

    Raises ValueError if the task does not exist or is not currently archived.
    """
    current = get_task(task_id)
    if current is None:
        raise ValueError(f"task '{task_id}' does not exist")
    if current["status"] != "archived":
        raise ValueError(f"task '{task_id}' is not archived (status='{current['status']}')")
    update_task_status(task_id, "pending")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_db.py::TestArchiveRestore -v`

Expected: 11 passed.

- [ ] **Step 5: Run full test_db.py to confirm no regression**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_db.py -q`

Expected: all previous tests still pass.

- [ ] **Step 6: Commit**

```bash
cd "D:/codeSpace/claudecode/stock_data/todos"
git add scripts/db.py tests/test_db.py
git commit -m "Add db.archive_goal/task + restore_goal/task with tests"
```

---

### Task 2: sync_md.py archived handling + scheduler.py filter

**Files:**
- Modify: `scripts/sync_md.py` (`STATUS_LABELS`, `sync_index_md`)
- Modify: `scripts/scheduler.py` (`list_eligible_tasks`)
- Modify: `tests/test_sync_md.py` (append `TestArchivedGoalInSync`)
- Modify: `tests/test_scheduler.py` (append `TestArchivedExclusion`)

**Interfaces:**
- Consumes: existing `STATUS_LABELS`, `_GROUP_ORDER`, `_group_and_sort`, `list_goals`, `list_tasks`, `group_tasks_by_goal`.
- Produces:
  - `STATUS_LABELS["archived"] = "已归档"` (new key)
  - `_GROUP_ORDER` unchanged (archived NOT added; archived goals never enter a group)
  - `sync_index_md`'s `by_status` counter excludes archived goals
  - `sync_index_md`'s "missing goal.md" warning is suppressed for archived goals
  - `scheduler.list_eligible_tasks` skips tasks with `status='archived'`

- [ ] **Step 1: Write the failing tests for sync_md.py archived handling**

Append to `tests/test_sync_md.py`:

```python
class TestArchivedGoalInSync:
    def _seed_db(self, tmp_path, monkeypatch, goals_spec):
        """Seed an isolated DB. goals_spec is list of dicts with slug/name/status.
        Each goal also creates a stub goals/<slug>/goal.md.
        """
        from db import _init_schema
        db_path = tmp_path / "todos.db"
        monkeypatch.setattr("db.DB_PATH", str(db_path))
        _init_schema()
        goals = []
        for g in goals_spec:
            db.create_goal(g["slug"], g["name"], "")
            if "status" in g and g["status"] != "active":
                db.update_goal_status(g["slug"], g["status"])
            (tmp_path / "goals" / g["slug"]).mkdir(parents=True, exist_ok=True)
            (tmp_path / "goals" / g["slug"] / "goal.md").write_text(
                f"# {g['name']}\n", encoding="utf-8"
            )
            goals.append(db.get_goal(g["slug"]))
        return goals

    def test_archived_excluded_from_index(self, tmp_path, monkeypatch):
        self._seed_db(tmp_path, monkeypatch, [
            {"slug": "alive", "name": "A", "status": "active"},
            {"slug": "dead", "name": "D", "status": "archived"},
        ])
        result = sync_md.sync_index_md(tmp_path / "goals")
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "[A](alive/goal.md)" in index
        assert "[D](dead/goal.md)" not in index

    def test_archived_excluded_from_by_status(self, tmp_path, monkeypatch):
        self._seed_db(tmp_path, monkeypatch, [
            {"slug": "alive", "name": "A", "status": "active"},
            {"slug": "dead", "name": "D", "status": "archived"},
        ])
        result = sync_md.sync_index_md(tmp_path / "goals")
        # synced_count includes archived (it IS a known DB row) but by_status does not
        assert result.synced_count == 2
        assert "archived" not in result.by_status
        assert result.by_status["active"] == 1

    def test_restore_makes_goal_reappear(self, tmp_path, monkeypatch):
        self._seed_db(tmp_path, monkeypatch, [
            {"slug": "x", "name": "X", "status": "active"},
        ])
        # archive
        db.archive_goal("x")
        sync_md.sync_index_md(tmp_path / "goals")
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "[X](x/goal.md)" not in index
        # restore
        db.restore_goal("x")
        sync_md.sync_index_md(tmp_path / "goals")
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "[X](x/goal.md)" in index

    def test_archived_with_missing_goal_md_no_warning(self, tmp_path, monkeypatch):
        """An archived goal whose goal.md is missing must NOT trigger the warning."""
        from db import _init_schema
        db_path = tmp_path / "todos.db"
        monkeypatch.setattr("db.DB_PATH", str(db_path))
        _init_schema()
        db.create_goal("archived-ghost", "G", "")
        db.update_goal_status("archived-ghost", "archived")
        # Note: NO goals/archived-ghost/ directory or goal.md created.
        result = sync_md.sync_index_md(tmp_path / "goals")
        assert not any("archived-ghost" in w for w in result.warnings)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_sync_md.py::TestArchivedGoalInSync -v`

Expected: FAIL — `STATUS_LABELS["archived"]` KeyError (current code only knows 3 statuses), and the by_status / warning suppression logic doesn't exist yet.

- [ ] **Step 3: Modify sync_md.py**

In `scripts/sync_md.py`:

1. Update `STATUS_LABELS` (around line 24-28) to add `archived`:

```python
STATUS_LABELS: dict[str, str] = {
    "active": "进行中",
    "paused": "已暂停",
    "completed": "已完成",
    "archived": "已归档",
}
```

2. In `sync_index_md` (around line 285-297), modify the `by_status` build to exclude archived:

Find:
```python
    by_status: dict[str, int] = {s: 0 for s in _GROUP_ORDER}
    for g in goals:
        by_status[g["status"]] = by_status.get(g["status"], 0) + 1
```

Replace with:
```python
    by_status: dict[str, int] = {s: 0 for s in _GROUP_ORDER}
    for g in goals:
        status = g["status"]
        if status not in by_status:
            # archived goals are counted by synced_count but not bucketed
            # into by_status (which tracks active/paused/completed)
            continue
        by_status[status] += 1
```

3. In `sync_index_md`'s orphan-detection loop (around line 257-267), suppress the "missing goal.md" warning for archived goals:

Find:
```python
    for entry in sorted(goals_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name not in db_slugs:
            warnings.append(
                f"goal dir 'goals/{entry.name}/' has no DB row — skipped"
            )
        elif not (entry / "goal.md").exists():
            warnings.append(f"goal '{entry.name}' has no goals/{entry.name}/goal.md")
```

Replace with:
```python
    goal_status_by_slug = {g["slug"]: g["status"] for g in goals}
    for entry in sorted(goals_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name not in db_slugs:
            warnings.append(
                f"goal dir 'goals/{entry.name}/' has no DB row — skipped"
            )
        elif (
            not (entry / "goal.md").exists()
            and goal_status_by_slug.get(entry.name) != "archived"
        ):
            warnings.append(f"goal '{entry.name}' has no goals/{entry.name}/goal.md")
```

- [ ] **Step 4: Run sync_md tests to verify they pass**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_sync_md.py -v`

Expected: all green (24 existing + 4 new = 28 passed).

- [ ] **Step 5: Write the failing test for scheduler.py archived filter**

Append to `tests/test_scheduler.py`:

```python
class TestArchivedExclusion:
    def test_list_eligible_tasks_excludes_archived(self, tmp_path, monkeypatch):
        from db import _init_schema
        db_path = tmp_path / "todos.db"
        monkeypatch.setattr("db.DB_PATH", str(db_path))
        _init_schema()
        db.create_goal("g", "G", "")
        db.create_task("g-T001", "g", 1, "pending task", 1.0, "[]", "pending")
        db.create_task("g-T002", "g", 2, "in_progress task", 1.0, "[]", "in_progress")
        db.create_task("g-T003", "g", 3, "done task", 1.0, "[]", "done")
        db.create_task("g-T004", "g", 4, "archived task", 1.0, "[]", "archived")

        eligible = scheduler.list_eligible_tasks()
        eligible_ids = {t["id"] for t in eligible}
        assert "g-T001" in eligible_ids      # pending: eligible
        assert "g-T002" in eligible_ids      # in_progress: eligible
        assert "g-T003" not in eligible_ids  # done: not eligible
        assert "g-T004" not in eligible_ids  # archived: NOT eligible (NEW)
```

- [ ] **Step 6: Run scheduler test to verify it fails**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_scheduler.py::TestArchivedExclusion -v`

Expected: FAIL — archived task is currently eligible because the filter doesn't check for archived.

- [ ] **Step 7: Modify scheduler.py**

In `scripts/scheduler.py:list_eligible_tasks` (around line 142-156), find the status filter:

```python
    eligible = [
        task for task in tasks
        if task["status"] in ("pending", "in_progress")
    ]
```

Replace with:

```python
    eligible = [
        task for task in tasks
        if task["status"] in ("pending", "in_progress")
        and task["status"] != "archived"
    ]
```

(Equivalently add `and task["status"] != "archived"` — both clauses are equivalent since "archived" is not in the existing tuple; but the explicit form documents intent.)

- [ ] **Step 8: Run all scheduler tests to verify green**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_scheduler.py -v`

Expected: all green.

- [ ] **Step 9: Run full suite to confirm no regression**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest -q`

Expected: `2 failed, 149 passed` (the 2 failures are the pre-existing `test_today_human_output` / `test_today_json_output`; 145 prior + 4 new sync-md + 1 new scheduler = 149 + 2 pre-existing failures).

- [ ] **Step 10: Commit**

```bash
cd "D:/codeSpace/claudecode/stock_data/todos"
git add scripts/sync_md.py scripts/scheduler.py tests/test_sync_md.py tests/test_scheduler.py
git commit -m "Sync-md/scheduler: exclude archived goals from index and eligibility"
```

---

### Task 3: CLI — `goal list` / `goal show` / `goal update` / `goal delete` / `goal restore`

**Files:**
- Modify: `scripts/cli.py` (add 5 subcommand bodies + parser registrations + dispatch)
- Modify: `tests/test_cli.py` (append `TestGoalListCli`, `TestGoalShowCli`, `TestGoalUpdateCli`, `TestGoalDeleteCli`, `TestGoalRestoreCli`)

**Interfaces:**
- Consumes: existing `_validate_slug`, `_emit_error`, `_autosync_index_md`, `to_json`, `db.list_goals`, `db.get_goal`, `db.update_goal_status`, `db.archive_goal`, `db.restore_goal`. Existing `_init_db` and `run_cli` from test_cli.py.
- Produces 5 new CLI subcommands:
  - `goal list [--status X] [--all] [--json]`
  - `goal show <slug> [--json]`
  - `goal update <slug> --status X [--json]` (triggers `_autosync_index_md` on real change; rejects `archived`)
  - `goal delete <slug> [--json]` (soft delete; triggers sync on real change)
  - `goal restore <slug> [--json]` (archived → active; triggers sync; fails on non-archived)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
# ----- helpers -----

def _seed_goals(db_path: Path, goals_spec):
    """Seed goals directly into a DB. goals_spec is list of dicts."""
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        for g in goals_spec:
            conn.execute(
                "INSERT INTO goals (slug, name, description, status, "
                "total_tasks, completed_tasks, created_at, updated_at) "
                "VALUES (?, ?, '', ?, 0, 0, '2026-08-05T00:00:00', "
                "'2026-08-05T00:00:00')",
                (g["slug"], g["name"], g["status"]),
            )
            (db_path.parent / "goals" / g["slug"]).mkdir(parents=True, exist_ok=True)
            (db_path.parent / "goals" / g["slug"] / "goal.md").write_text(
                f"# {g['name']}\n", encoding="utf-8"
            )
        conn.commit()


# ----- TestGoalListCli -----

class TestGoalListCli:
    def test_default_hides_archived(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [
            {"slug": "alive", "name": "Alive", "status": "active"},
            {"slug": "dead", "name": "Dead", "status": "archived"},
        ])
        result = run_cli(["goal", "list"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "alive" in result.stdout
        assert "dead" not in result.stdout

    def test_all_includes_archived(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [
            {"slug": "alive", "name": "Alive", "status": "active"},
            {"slug": "dead", "name": "Dead", "status": "archived"},
        ])
        result = run_cli(["goal", "list", "--all"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        assert "alive" in result.stdout
        assert "dead" in result.stdout

    def test_status_query_filters(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [
            {"slug": "a1", "name": "A1", "status": "active"},
            {"slug": "a2", "name": "A2", "status": "active"},
            {"slug": "dead", "name": "Dead", "status": "archived"},
        ])
        result = run_cli(["goal", "list", "--status", "archived"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        assert "a1" not in result.stdout
        assert "dead" in result.stdout

    def test_json_output_shape(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [
            {"slug": "x", "name": "X", "status": "active"},
        ])
        result = run_cli(["goal", "list", "--json"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["slug"] == "x"


# ----- TestGoalShowCli -----

class TestGoalShowCli:
    def test_show_existing(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        result = run_cli(["goal", "show", "x"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "x" in result.stdout
        assert "active" in result.stdout

    def test_show_json(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        result = run_cli(["goal", "show", "x", "--json"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["slug"] == "x"
        assert data["status"] == "active"

    def test_show_missing_exits_2(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        result = run_cli(["goal", "show", "missing"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2
        assert "missing" in result.stderr


# ----- TestGoalUpdateCli -----

class TestGoalUpdateCli:
    def test_update_status_triggers_sync(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        result = run_cli(["goal", "update", "x", "--status", "paused"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        # sync-md auto-trigger wrote index.md with new status
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "已暂停" in index
        assert "[X](x/goal.md)" in index

    def test_update_to_archived_rejected(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        result = run_cli(["goal", "update", "x", "--status", "archived"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2
        assert "delete" in result.stderr  # hint pointing to goal delete

    def test_update_noop_no_sync(self, tmp_path):
        """Updating to the same status should not write index.md (idempotent reapply)."""
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        # Pre-create index.md with a sentinel; verify it is NOT touched on noop.
        (tmp_path / "goals" / "index.md").write_text(
            "# SENTINEL — must remain unchanged\n", encoding="utf-8"
        )
        result = run_cli(["goal", "update", "x", "--status", "active"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "SENTINEL" in index  # untouched

    def test_update_invalid_status_rejected(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        result = run_cli(["goal", "update", "x", "--status", "bogus"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2


# ----- TestGoalDeleteCli -----

class TestGoalDeleteCli:
    def test_delete_archives_and_syncs(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        result = run_cli(["goal", "delete", "x"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        # index.md must no longer contain the deleted goal
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "[X](x/goal.md)" not in index
        # goal.md still on disk (soft delete preserves file)
        assert (tmp_path / "goals" / "x" / "goal.md").exists()

    def test_delete_idempotent_no_sync(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        # First delete archives
        r1 = run_cli(["goal", "delete", "x"], db_path=db_path, cwd=tmp_path)
        assert r1.returncode == 0
        # Pre-write sentinel
        (tmp_path / "goals" / "index.md").write_text(
            "# SENTINEL — second delete must not touch this\n", encoding="utf-8"
        )
        # Second delete: already archived, no-op, no sync
        r2 = run_cli(["goal", "delete", "x"], db_path=db_path, cwd=tmp_path)
        assert r2.returncode == 0
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "SENTINEL" in index

    def test_delete_missing_exits_2(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        result = run_cli(["goal", "delete", "nope"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2


# ----- TestGoalRestoreCli -----

class TestGoalRestoreCli:
    def test_restore_archived_to_active(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "archived"}])
        result = run_cli(["goal", "restore", "x"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "[X](x/goal.md)" in index
        assert "进行中" in index

    def test_restore_non_archived_exits_2(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        result = run_cli(["goal", "restore", "x"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2
        assert "not archived" in result.stderr

    def test_restore_missing_exits_2(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        result = run_cli(["goal", "restore", "nope"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2
```

Add at the top of `tests/test_cli.py` if not already present: `import json`, `import sqlite3` (likely already imported elsewhere — verify before adding).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_cli.py::TestGoalListCli tests/test_cli.py::TestGoalShowCli tests/test_cli.py::TestGoalUpdateCli tests/test_cli.py::TestGoalDeleteCli tests/test_cli.py::TestGoalRestoreCli -v`

Expected: all FAIL with argparse `unrecognized arguments: list` (etc.) for the goal subcommands.

- [ ] **Step 3: Implement the 5 goal subcommands**

Open `scripts/cli.py`. The `goal_sub` parser is already in `_build_parser` (around line 386-391). Replace the existing `ga` (goal add) block with an extended block that adds list/show/update/delete/restore as siblings of `add`:

Find:
```python
    goal_p = sub.add_parser("goal", help="Goal operations")
    goal_sub = goal_p.add_subparsers(dest="goal_command", required=True)
    ga = goal_sub.add_parser("add", help="Add a new goal")
    ga.add_argument("slug")
    ga.add_argument("name")
    ga.add_argument("--description", default="")
```

Replace with:
```python
    goal_p = sub.add_parser("goal", help="Goal operations")
    goal_sub = goal_p.add_subparsers(dest="goal_command", required=True)

    ga = goal_sub.add_parser("add", help="Add a new goal")
    ga.add_argument("slug")
    ga.add_argument("name")
    ga.add_argument("--description", default="")

    gl = goal_sub.add_parser("list", help="List goals (default: hide archived)")
    gl.add_argument("--status", choices=["active", "paused", "completed", "archived"])
    gl.add_argument("--all", action="store_true", help="Include archived goals")

    gsh = goal_sub.add_parser("show", help="Show a single goal")
    gsh.add_argument("slug")

    gu = goal_sub.add_parser("update", help="Update a goal's status")
    gu.add_argument("slug")
    gu.add_argument(
        "--status",
        required=True,
        choices=["active", "paused", "completed"],
        help="New status (use 'goal delete' for archived)",
    )

    gd = goal_sub.add_parser("delete", help="Soft-delete a goal (sets status=archived)")
    gd.add_argument("slug")

    gr = goal_sub.add_parser("restore", help="Restore an archived goal to active")
    gr.add_argument("slug")
```

Add the 5 new subcommand bodies. Place them after `subcommand_goal_add` (around line 219). Keep them in the same order as the parser registration:

```python
def subcommand_goal_list(args, as_json: bool) -> int:
    """List goals. Default excludes archived; --all includes them; --status filters to one."""
    if args.all:
        goals = db.list_goals()
    elif args.status:
        goals = db.list_goals(status=args.status)
    else:
        goals = db.list_goals(status="active")
    if as_json:
        print(to_json(goals))
    else:
        if not goals:
            print("(no goals)")
            return 0
        for g in goals:
            label = {"active": "进行中", "paused": "已暂停",
                     "completed": "已完成", "archived": "已归档"}.get(g["status"], g["status"])
            print(f"- {g['slug']:<20} {label}")
    return 0


def subcommand_goal_show(args, as_json: bool) -> int:
    """Show one goal by slug. Exits 2 if not found."""
    goal = db.get_goal(args.slug)
    if goal is None:
        _emit_error(f"goal '{args.slug}' not found", code=2)
    if as_json:
        print(to_json(goal))
    else:
        label_map = {"active": "进行中", "paused": "已暂停",
                     "completed": "已完成", "archived": "已归档"}
        print(f"slug:   {goal['slug']}")
        print(f"name:   {goal['name']}")
        print(f"status: {label_map.get(goal['status'], goal['status'])}")
        print(f"description: {goal.get('description') or '(none)'}")
    return 0


def subcommand_goal_update(args, as_json: bool) -> int:
    """Update a goal's status. Rejects --status archived. Sync-md only on real change."""
    if args.status == "archived":
        _emit_error(
            "use 'goal delete <slug>' to archive (rejects --status archived here to avoid accidents)",
            code=2,
        )
    current = db.get_goal(args.slug)
    if current is None:
        _emit_error(f"goal '{args.slug}' not found", code=2)
    changed = current["status"] != args.status
    db.update_goal_status(args.slug, args.status)
    if changed:
        _autosync_index_md()
    if as_json:
        print(to_json({"slug": args.slug, "status": args.status, "changed": changed}))
    else:
        verb = "updated" if changed else "no change"
        print(f"Goal '{args.slug}' {verb} → {args.status}.")
    return 0


def subcommand_goal_delete(args, as_json: bool) -> int:
    """Soft-delete: sets status='archived'. Sync-md only on real change."""
    try:
        changed = db.archive_goal(args.slug)
    except ValueError as exc:
        _emit_error(str(exc), code=2)
    if changed:
        _autosync_index_md()
    if as_json:
        print(to_json({"slug": args.slug, "archived": changed}))
    else:
        verb = "archived" if changed else "already archived"
        print(f"Goal '{args.slug}' {verb}.")
    return 0


def subcommand_goal_restore(args, as_json: bool) -> int:
    """Restore archived goal to active. Errors if not archived."""
    try:
        db.restore_goal(args.slug)
    except ValueError as exc:
        _emit_error(str(exc), code=2)
    _autosync_index_md()
    if as_json:
        print(to_json({"slug": args.slug, "status": "active"}))
    else:
        print(f"Goal '{args.slug}' restored to active.")
    return 0
```

- [ ] **Step 4: Wire dispatch in `run()`**

In `scripts/cli.py:run()` (around line 432-470), find the `goal` branch:

```python
            elif parsed.command == "goal":
                return _dispatch_goal(parsed, as_json)
```

If `_dispatch_goal` exists, replace it with an inline dispatcher that matches on `parsed.goal_command`:

```python
            elif parsed.command == "goal":
                if parsed.goal_command == "add":
                    return subcommand_goal_add(parsed, as_json)
                if parsed.goal_command == "list":
                    return subcommand_goal_list(parsed, as_json)
                if parsed.goal_command == "show":
                    return subcommand_goal_show(parsed, as_json)
                if parsed.goal_command == "update":
                    return subcommand_goal_update(parsed, as_json)
                if parsed.goal_command == "delete":
                    return subcommand_goal_delete(parsed, as_json)
                if parsed.goal_command == "restore":
                    return subcommand_goal_restore(parsed, as_json)
                _emit_error(f"unknown goal command: {parsed.goal_command}", code=1)
```

If `_dispatch_goal` is NOT the pattern (inspect the current `run()` body to confirm), adapt accordingly: the goal must dispatch on both `parsed.command == "goal"` AND `parsed.goal_command`.

- [ ] **Step 5: Run the new goal CLI tests to verify they pass**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_cli.py::TestGoalListCli tests/test_cli.py::TestGoalShowCli tests/test_cli.py::TestGoalUpdateCli tests/test_cli.py::TestGoalDeleteCli tests/test_cli.py::TestGoalRestoreCli -v`

Expected: 17 passed.

- [ ] **Step 6: Run full test_cli.py to confirm no regression**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_cli.py -q`

Expected: 2 failed, 62 passed (45 prior + 17 new = 62, plus the 2 pre-existing `test_today_*` failures).

- [ ] **Step 7: Commit**

```bash
cd "D:/codeSpace/claudecode/stock_data/todos"
git add scripts/cli.py tests/test_cli.py
git commit -m "CLI: add goal list/show/update/delete/restore with sync-md integration"
```

---

### Task 4: CLI — `task list` / `task show` / `task delete` / `task restore`

**Files:**
- Modify: `scripts/cli.py` (add 4 subcommand bodies + parser registrations + dispatch)
- Modify: `tests/test_cli.py` (append `TestTaskListCli`, `TestTaskShowCli`, `TestTaskDeleteCli`, `TestTaskRestoreCli`)

**Interfaces:**
- Consumes: existing `db.list_tasks(goal_slug=..., status=...)`, `db.get_task`, `db.archive_task`, `db.restore_task`.
- Produces 4 new CLI subcommands:
  - `task list [--goal X] [--status X] [--all] [--json]`
  - `task show <id> [--json]`
  - `task delete <id> [--json]` (soft delete; triggers sync on real change)
  - `task restore <id> [--json]` (archived → pending; triggers sync; fails on non-archived)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
# ----- TestTaskListCli -----

class TestTaskListCli:
    def _seed(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            for g in [{"slug": "g1", "name": "G1"}, {"slug": "g2", "name": "G2"}]:
                conn.execute(
                    "INSERT INTO goals (slug, name, description, status, "
                    "total_tasks, completed_tasks, created_at, updated_at) "
                    "VALUES (?, ?, '', 'active', 0, 0, '2026-08-05T00:00:00', "
                    "'2026-08-05T00:00:00')",
                    (g["slug"], g["name"]),
                )
                (tmp_path / "goals" / g["slug"]).mkdir(parents=True, exist_ok=True)
                (tmp_path / "goals" / g["slug"] / "goal.md").write_text(
                    f"# {g['name']}\n", encoding="utf-8"
                )
            tasks = [
                ("g1-T001", "g1", 1, "pending", "pending task"),
                ("g1-T002", "g1", 2, "done", "done task"),
                ("g2-T001", "g2", 1, "pending", "another pending"),
                ("g1-T003", "g1", 3, "archived", "archived task"),
            ]
            for tid, slug, seq, status, title in tasks:
                conn.execute(
                    "INSERT INTO tasks (id, goal_slug, sequence, title, "
                    "description, estimated_hours, depends_on, status, "
                    "last_reminded_at, completed_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, '', 1.0, '[]', ?, NULL, NULL, "
                    "'2026-08-05T00:00:00', '2026-08-05T00:00:00')",
                    (tid, slug, seq, status),
                )
            conn.commit()
        return db_path

    def test_default_hides_archived(self, tmp_path):
        db_path = self._seed(tmp_path)
        result = run_cli(["task", "list"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "g1-T001" in result.stdout
        assert "g1-T002" in result.stdout
        assert "g1-T003" not in result.stdout  # archived excluded

    def test_all_includes_archived(self, tmp_path):
        db_path = self._seed(tmp_path)
        result = run_cli(["task", "list", "--all"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        assert "g1-T003" in result.stdout

    def test_goal_filter(self, tmp_path):
        db_path = self._seed(tmp_path)
        result = run_cli(["task", "list", "--goal", "g1"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        assert "g1-T001" in result.stdout
        assert "g2-T001" not in result.stdout

    def test_status_query(self, tmp_path):
        db_path = self._seed(tmp_path)
        result = run_cli(["task", "list", "--status", "done"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        assert "g1-T002" in result.stdout
        assert "g1-T001" not in result.stdout

    def test_json_output(self, tmp_path):
        db_path = self._seed(tmp_path)
        result = run_cli(["task", "list", "--json"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)


# ----- TestTaskShowCli -----

class TestTaskShowCli:
    def _seed_one(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO goals (slug, name, description, status, "
                "total_tasks, completed_tasks, created_at, updated_at) "
                "VALUES ('g', 'G', '', 'active', 0, 0, '2026-08-05T00:00:00', "
                "'2026-08-05T00:00:00')"
            )
            conn.execute(
                "INSERT INTO tasks (id, goal_slug, sequence, title, "
                "description, estimated_hours, depends_on, status, "
                "last_reminded_at, completed_at, created_at, updated_at) "
                "VALUES ('g-T001', 'g', 1, 'hello task', '', 1.0, '[]', "
                "'pending', NULL, NULL, '2026-08-05T00:00:00', '2026-08-05T00:00:00')"
            )
            conn.commit()
        (tmp_path / "goals" / "g").mkdir(parents=True, exist_ok=True)
        (tmp_path / "goals" / "g" / "goal.md").write_text("# G\n", encoding="utf-8")
        return db_path

    def test_show_existing(self, tmp_path):
        db_path = self._seed_one(tmp_path)
        result = run_cli(["task", "show", "g-T001"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "g-T001" in result.stdout
        assert "hello task" in result.stdout

    def test_show_missing_exits_2(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        result = run_cli(["task", "show", "nope"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2


# ----- TestTaskDeleteCli -----

class TestTaskDeleteCli:
    def _seed_one(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO goals (slug, name, description, status, "
                "total_tasks, completed_tasks, created_at, updated_at) "
                "VALUES ('g', 'G', '', 'active', 0, 0, '2026-08-05T00:00:00', "
                "'2026-08-05T00:00:00')"
            )
            for tid, status in [("g-T001", "pending"), ("g-T002", "done")]:
                conn.execute(
                    "INSERT INTO tasks (id, goal_slug, sequence, title, "
                    "description, estimated_hours, depends_on, status, "
                    "last_reminded_at, completed_at, created_at, updated_at) "
                    "VALUES (?, 'g', ?, '', 1.0, '[]', ?, NULL, NULL, "
                    "'2026-08-05T00:00:00', '2026-08-05T00:00:00')",
                    (tid, 1 if tid.endswith("001") else 2, status),
                )
            conn.commit()
        (tmp_path / "goals" / "g").mkdir(parents=True, exist_ok=True)
        (tmp_path / "goals" / "g" / "goal.md").write_text("# G\n", encoding="utf-8")
        return db_path

    def test_delete_archives_and_updates_pct(self, tmp_path):
        db_path = self._seed_one(tmp_path)
        result = run_cli(["task", "delete", "g-T001"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        # The goal's pct in index.md should now reflect: 1 done / 2 non-archived = 50%
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "完成率 50%" in index

    def test_delete_idempotent(self, tmp_path):
        db_path = self._seed_one(tmp_path)
        r1 = run_cli(["task", "delete", "g-T001"], db_path=db_path, cwd=tmp_path)
        assert r1.returncode == 0
        (tmp_path / "goals" / "index.md").write_text(
            "# SENTINEL\n", encoding="utf-8"
        )
        r2 = run_cli(["task", "delete", "g-T001"], db_path=db_path, cwd=tmp_path)
        assert r2.returncode == 0
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "SENTINEL" in index

    def test_delete_missing_exits_2(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        result = run_cli(["task", "delete", "nope"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2


# ----- TestTaskRestoreCli -----

class TestTaskRestoreCli:
    def test_restore_archived_to_pending(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO goals (slug, name, description, status, "
                "total_tasks, completed_tasks, created_at, updated_at) "
                "VALUES ('g', 'G', '', 'active', 0, 0, '2026-08-05T00:00:00', "
                "'2026-08-05T00:00:00')"
            )
            conn.execute(
                "INSERT INTO tasks (id, goal_slug, sequence, title, "
                "description, estimated_hours, depends_on, status, "
                "last_reminded_at, completed_at, created_at, updated_at) "
                "VALUES ('g-T001', 'g', 1, '', '', 1.0, '[]', 'archived', "
                "NULL, NULL, '2026-08-05T00:00:00', '2026-08-05T00:00:00')"
            )
            conn.commit()
        (tmp_path / "goals" / "g").mkdir(parents=True, exist_ok=True)
        (tmp_path / "goals" / "g" / "goal.md").write_text("# G\n", encoding="utf-8")
        result = run_cli(["task", "restore", "g-T001"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        # 0 of 1 done = 0% — but the goal is back in the active group
        assert "[G](g/goal.md)" in index
        assert "完成率 0%" in index

    def test_restore_non_archived_exits_2(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO goals (slug, name, description, status, "
                "total_tasks, completed_tasks, created_at, updated_at) "
                "VALUES ('g', 'G', '', 'active', 0, 0, '2026-08-05T00:00:00', "
                "'2026-08-05T00:00:00')"
            )
            conn.execute(
                "INSERT INTO tasks (id, goal_slug, sequence, title, "
                "description, estimated_hours, depends_on, status, "
                "last_reminded_at, completed_at, created_at, updated_at) "
                "VALUES ('g-T001', 'g', 1, '', '', 1.0, '[]', 'pending', "
                "NULL, NULL, '2026-08-05T00:00:00', '2026-08-05T00:00:00')"
            )
            conn.commit()
        result = run_cli(["task", "restore", "g-T001"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2
        assert "not archived" in result.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_cli.py::TestTaskListCli tests/test_cli.py::TestTaskShowCli tests/test_cli.py::TestTaskDeleteCli tests/test_cli.py::TestTaskRestoreCli -v`

Expected: all FAIL with `unrecognized arguments: list` (etc.) from argparse.

- [ ] **Step 3: Extend the `task_sub` parser**

In `scripts/cli.py:_build_parser()`, find the task parser block (around line 393-403):

```python
    task_p = sub.add_parser("task", help="Task operations")
    task_sub = task_p.add_subparsers(dest="task_command", required=True)
    ta = task_sub.add_parser("add", help="Add a new task")
    ta.add_argument("id")
    ta.add_argument("slug")
    ta.add_argument("sequence", type=int)
    ta.add_argument("title")
    ta.add_argument("--hours", type=float, default=1.0)
    ta.add_argument("--depends-on", default="[]")
    tu = task_sub.add_parser("update", help="Update a task's status")
    tu.add_argument("task_id")
    tu.add_argument("status")
```

Add `list`, `show`, `delete`, `restore` siblings after `update`:

```python
    tl = task_sub.add_parser("list", help="List tasks (default: hide archived)")
    tl.add_argument("--goal")
    tl.add_argument("--status", choices=["pending", "in_progress", "done", "skipped", "archived"])
    tl.add_argument("--all", action="store_true", help="Include archived tasks")

    tsh = task_sub.add_parser("show", help="Show a single task")
    tsh.add_argument("task_id")

    td = task_sub.add_parser("delete", help="Soft-delete a task (sets status=archived)")
    td.add_argument("task_id")

    tr = task_sub.add_parser("restore", help="Restore an archived task to pending")
    tr.add_argument("task_id")
```

- [ ] **Step 4: Add 4 task subcommand bodies**

After `subcommand_task_update` in `scripts/cli.py`, append:

```python
def subcommand_task_list(args, as_json: bool) -> int:
    """List tasks. Default excludes archived; --all includes; --goal / --status filter."""
    if args.all:
        tasks = db.list_tasks(goal_slug=args.goal)
    elif args.status:
        tasks = db.list_tasks(goal_slug=args.goal, status=args.status)
    else:
        tasks = db.list_tasks(goal_slug=args.goal)
        tasks = [t for t in tasks if t["status"] != "archived"]
    if as_json:
        print(to_json(tasks))
    else:
        if not tasks:
            print("(no tasks)")
            return 0
        label_map = {"pending": "待办", "in_progress": "进行中",
                     "done": "已完成", "skipped": "已跳过", "archived": "已归档"}
        for t in tasks:
            label = label_map.get(t["status"], t["status"])
            print(f"- {t['id']:<20} [{t['goal_slug']}] {label}")
    return 0


def subcommand_task_show(args, as_json: bool) -> int:
    """Show one task. Exits 2 if not found."""
    task = db.get_task(args.task_id)
    if task is None:
        _emit_error(f"task '{args.task_id}' not found", code=2)
    if as_json:
        print(to_json(task))
    else:
        label_map = {"pending": "待办", "in_progress": "进行中",
                     "done": "已完成", "skipped": "已跳过", "archived": "已归档"}
        print(f"id:     {task['id']}")
        print(f"goal:   {task['goal_slug']}")
        print(f"seq:    {task['sequence']}")
        print(f"title:  {task['title']}")
        print(f"hours:  {task.get('estimated_hours')}")
        print(f"status: {label_map.get(task['status'], task['status'])}")
    return 0


def subcommand_task_delete(args, as_json: bool) -> int:
    """Soft-delete: sets status='archived'. Sync-md only on real change."""
    try:
        changed = db.archive_task(args.task_id)
    except ValueError as exc:
        _emit_error(str(exc), code=2)
    if changed:
        _autosync_index_md()
    if as_json:
        print(to_json({"task_id": args.task_id, "archived": changed}))
    else:
        verb = "archived" if changed else "already archived"
        print(f"Task '{args.task_id}' {verb}.")
    return 0


def subcommand_task_restore(args, as_json: bool) -> int:
    """Restore archived task to pending. Errors if not archived."""
    try:
        db.restore_task(args.task_id)
    except ValueError as exc:
        _emit_error(str(exc), code=2)
    _autosync_index_md()
    if as_json:
        print(to_json({"task_id": args.task_id, "status": "pending"}))
    else:
        print(f"Task '{args.task_id}' restored to pending.")
    return 0
```

- [ ] **Step 5: Wire dispatch in `run()`**

In the `task` branch of `run()`, dispatch on `parsed.task_command`:

```python
            elif parsed.command == "task":
                if parsed.task_command == "add":
                    return subcommand_task_add(parsed, as_json)
                if parsed.task_command == "list":
                    return subcommand_task_list(parsed, as_json)
                if parsed.task_command == "show":
                    return subcommand_task_show(parsed, as_json)
                if parsed.task_command == "update":
                    return subcommand_task_update(parsed, as_json)
                if parsed.task_command == "delete":
                    return subcommand_task_delete(parsed, as_json)
                if parsed.task_command == "restore":
                    return subcommand_task_restore(parsed, as_json)
                _emit_error(f"unknown task command: {parsed.task_command}", code=1)
```

- [ ] **Step 6: Run the new task CLI tests to verify they pass**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_cli.py::TestTaskListCli tests/test_cli.py::TestTaskShowCli tests/test_cli.py::TestTaskDeleteCli tests/test_cli.py::TestTaskRestoreCli -v`

Expected: 12 passed.

- [ ] **Step 7: Run full test_cli.py to confirm no regression**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_cli.py -q`

Expected: 2 failed, 74 passed (62 prior + 12 new).

- [ ] **Step 8: Commit**

```bash
cd "D:/codeSpace/claudecode/stock_data/todos"
git add scripts/cli.py tests/test_cli.py
git commit -m "CLI: add task list/show/delete/restore with sync-md integration"
```

---

### Task 5: Dashboard — index filter + goal_detail archived banner + /task/<id> route

**Files:**
- Modify: `dashboard/app.py` (3 route changes: index parsing, goal_detail banner, new /task/<id> route)
- Modify: `dashboard/templates/index.html` (archived link in header)
- Modify: `dashboard/templates/goal_detail.html` (archived banner)
- Create: `dashboard/templates/task_detail.html` (new template)
- Modify: `tests/test_dashboard.py` (append 7 tests)

**Interfaces:**
- Consumes: existing `db.list_goals`, `db.list_tasks`, `db.get_goal`, `db.get_task`, the Flask app factory.
- Produces:
  - `GET /` accepts `?all=1` / `?status=<key>` query string; passes filter to template
  - `GET /goal/<slug>` passes `is_archived` to template
  - `GET /task/<id>` new route, renders `task_detail.html`, 404 if not found

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard.py`:

```python
class TestArchivedInDashboard:
    def _setup_app(self, monkeypatch, tmp_path):
        """Create an app + isolated DB. Returns the Flask test client."""
        from db import _init_schema
        db_path = tmp_path / "todos.db"
        monkeypatch.setattr("db.DB_PATH", str(db_path))
        _init_schema()
        db.create_goal("alive", "Alive", "")
        db.create_goal("dead", "Dead", "")
        db.update_goal_status("dead", "archived")
        from dashboard.app import create_app
        app = create_app()
        app.config["TESTING"] = True
        return app.test_client()

    def test_index_default_hides_archived(self, tmp_path, monkeypatch):
        client = self._setup_app(monkeypatch, tmp_path)
        rv = client.get("/")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        assert "Alive" in body
        assert "Dead" not in body

    def test_index_show_all_includes_archived(self, tmp_path, monkeypatch):
        client = self._setup_app(monkeypatch, tmp_path)
        rv = client.get("/?all=1")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        assert "Alive" in body
        assert "Dead" in body

    def test_index_status_query(self, tmp_path, monkeypatch):
        client = self._setup_app(monkeypatch, tmp_path)
        rv = client.get("/?status=archived")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        assert "Dead" in body
        assert "Alive" not in body

    def test_goal_detail_archived_banner(self, tmp_path, monkeypatch):
        client = self._setup_app(monkeypatch, tmp_path)
        rv = client.get("/goal/dead")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        # Banner text mentions archived + restoration hint
        assert "已归档" in body or "archived" in body.lower()
        assert "restore" in body.lower() or "goal restore" in body


class TestTaskDetail:
    def _setup(self, monkeypatch, tmp_path):
        from db import _init_schema
        db_path = tmp_path / "todos.db"
        monkeypatch.setattr("db.DB_PATH", str(db_path))
        _init_schema()
        db.create_goal("g", "G", "")
        db.create_task("g-T001", "g", 1, "hello task", 2.0, "[]", "pending")
        db.update_task_status("g-T001", "archived")
        from dashboard.app import create_app
        app = create_app()
        app.config["TESTING"] = True
        return app.test_client()

    def test_task_detail_basic(self, tmp_path, monkeypatch):
        client = self._setup(monkeypatch, tmp_path)
        rv = client.get("/task/g-T001")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        assert "g-T001" in body
        assert "hello task" in body
        # parent goal link
        assert "g" in body

    def test_task_detail_archived_banner(self, tmp_path, monkeypatch):
        client = self._setup(monkeypatch, tmp_path)
        rv = client.get("/task/g-T001")
        body = rv.get_data(as_text=True)
        # Banner mentions archived for the task
        assert "已归档" in body or "archived" in body.lower()

    def test_task_detail_404(self, tmp_path, monkeypatch):
        from db import _init_schema
        db_path = tmp_path / "todos.db"
        monkeypatch.setattr("db.DB_PATH", str(db_path))
        _init_schema()
        from dashboard.app import create_app
        app = create_app()
        app.config["TESTING"] = True
        client = app.test_client()
        rv = client.get("/task/does-not-exist")
        assert rv.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_dashboard.py::TestArchivedInDashboard tests/test_dashboard.py::TestTaskDetail -v`

Expected: FAIL — index does not yet respect query string filters; goal_detail does not show archived banner; /task/<id> route returns 404 (no route).

- [ ] **Step 3: Modify the index route**

In `dashboard/app.py`, find the `GET /` route handler (the first `@flask_app.get(...)` block around line 185). It currently calls `_goal_row(goal)` for each goal and renders `index.html`. Modify it to:

1. Read `request.args.get("all")` and `request.args.get("status")`.
2. Filter the `goals` list accordingly before iterating.

Concretely, find the body that fetches goals (it currently does something like `goals = db.list_goals()`) and wrap it:

```python
from flask import request  # add to the existing flask import line

@flask_app.get("/")
def index():
    show_all = request.args.get("all") == "1"
    status_filter = request.args.get("status")
    if status_filter:
        goals = db.list_goals(status=status_filter)
    elif show_all:
        goals = db.list_goals()
    else:
        goals = db.list_goals(status="active")
    rows = [_goal_row(g) for g in goals]
    return render_template(
        "index.html",
        rows=rows,
        show_all=show_all,
        status_filter=status_filter,
    )
```

If the existing code does the fetch and render in different ways, adapt: the key invariant is that `index.html` receives `rows`, `show_all`, `status_filter` so it can render the header links.

- [ ] **Step 4: Modify the goal_detail route**

In `dashboard/app.py`, find `GET /goal/<slug>` (around line 190). Add `is_archived` to the template context:

```python
@flask_app.get("/goal/<slug>")
def goal_detail(slug):
    goal = db.get_goal(slug)
    if goal is None:
        return render_template("error.html", message=f"goal '{slug}' not found"), 404
    tasks = db.list_tasks(goal_slug=slug)
    completed = sum(t["status"] == "done" for t in tasks)
    current = next((t for t in tasks if t["status"] == "in_progress"), None)
    return render_template(
        "goal_detail.html",
        goal=goal,
        tasks=tasks,
        completed=completed,
        current=current,
        is_archived=(goal["status"] == "archived"),
    )
```

(Adjust to the existing structure; the load-bearing change is `is_archived=(goal["status"] == "archived")` being passed.)

- [ ] **Step 5: Add the /task/<id> route**

In `dashboard/app.py`, after the goal_detail route, append:

```python
@flask_app.get("/task/<task_id>")
def task_detail(task_id):
    task = db.get_task(task_id)
    if task is None:
        return render_template("error.html", message=f"task '{task_id}' not found"), 404
    parent = db.get_goal(task["goal_slug"])
    return render_template(
        "task_detail.html",
        task=task,
        parent=parent,
        is_archived=(task["status"] == "archived"),
    )
```

- [ ] **Step 6: Modify `index.html` template**

Open `dashboard/templates/index.html`. Find the header / top of the goals list. Insert a row with two links:

```html
<div class="filter-links">
  {% if status_filter == 'archived' %}
    <span>显示：已归档</span>
    <a href="/">显示进行中</a>
  {% elif show_all %}
    <span>显示：全部</span>
    <a href="/">只显示进行中</a>
    <a href="/?status=archived">查看已归档</a>
  {% else %}
    <a href="/?all=1">显示全部</a>
    <a href="/?status=archived">查看已归档</a>
  {% endif %}
</div>
```

(Adjust styling/positioning to match the existing template; the logic is the load-bearing part.)

- [ ] **Step 7: Modify `goal_detail.html` template**

Open `dashboard/templates/goal_detail.html`. At the top of the goal info block, insert:

```html
{% if is_archived %}
<div class="banner banner-archived">
  此目标已归档。可在 CLI 用 <code>goal restore {{ goal.slug }}</code> 恢复。
</div>
{% endif %}
```

- [ ] **Step 8: Create `task_detail.html` template**

Create `dashboard/templates/task_detail.html`:

```html
{% extends "base.html" %}
{% block title %}Task {{ task.id }}{% endblock %}
{% block content %}
<h1>{{ task.id }}</h1>
{% if is_archived %}
<div class="banner banner-archived">
  此任务已归档。可在 CLI 用 <code>task restore {{ task.id }}</code> 恢复。
</div>
{% endif %}
<dl>
  <dt>id</dt><dd>{{ task.id }}</dd>
  <dt>goal</dt><dd><a href="/goal/{{ task.goal_slug }}">{{ task.goal_slug }}</a></dd>
  <dt>sequence</dt><dd>{{ task.sequence }}</dd>
  <dt>title</dt><dd>{{ task.title }}</dd>
  <dt>hours</dt><dd>{{ task.estimated_hours }}</dd>
  <dt>status</dt><dd>{{ task.status }}</dd>
</dl>
{% endblock %}
```

(Adjust to extend whatever layout `base.html` defines; if `base.html` uses Jinja block names other than `title` / `content`, adapt.)

- [ ] **Step 9: Run dashboard tests to verify they pass**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_dashboard.py -v`

Expected: all green (existing tests + 7 new = ~32+ passed; exact count depends on existing test count).

- [ ] **Step 10: Commit**

```bash
cd "D:/codeSpace/claudecode/stock_data/todos"
git add dashboard/app.py dashboard/templates/index.html dashboard/templates/goal_detail.html dashboard/templates/task_detail.html tests/test_dashboard.py
git commit -m "Dashboard: archived filter on /, archived banner on goal_detail, new /task/<id> route"
```

---

### Task 6: README + final regression + smoke verification

**Files:**
- Modify: `README.md` (add example block for new subcommands under "### CLI")
- Verify: full test suite, manual end-to-end smoke

- [ ] **Step 1: Add a new example block to README**

Open `README.md`. Find the "### CLI" subsection inside "Common commands" (around line 56). After the `sync-md` block added by Item 5, append a fenced-block example for the CRUD补全 subcommands:

````markdown
```bash
# List goals (default hides archived)
python scripts/cli.py goal list
python scripts/cli.py goal list --all
python scripts/cli.py goal list --status archived
python scripts/cli.py goal list --json

# Show one goal
python scripts/cli.py goal show example-goal
python scripts/cli.py goal show example-goal --json

# Change a goal's status (use 'paused' / 'completed'; not 'archived')
python scripts/cli.py goal update example-goal --status paused
python scripts/cli.py goal update example-goal --status completed --json

# Soft-delete (archive) and restore
python scripts/cli.py goal delete example-goal
python scripts/cli.py goal restore example-goal

# Same surface for tasks
python scripts/cli.py task list
python scripts/cli.py task list --goal example-goal
python scripts/cli.py task list --status done --all
python scripts/cli.py task show example-goal-T001
python scripts/cli.py task delete example-goal-T001
python scripts/cli.py task restore example-goal-T001
```
````

- [ ] **Step 2: Run the full test suite for regression**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest -q`

Expected: `2 failed, ~185 passed`. The 2 failures are the pre-existing `test_today_human_output` and `test_today_json_output` (wall-clock-dependent, unrelated to this item).

Verify `git status --short` shows **no modifications to `goals/index.md`** after the run (Item 5 C1 regression guard: `TODO_GOALS_DIR` keeps subprocess writes isolated).

- [ ] **Step 3: Run a manual end-to-end smoke against the real DB**

The real DB lives at `data/todos.db` (from Item 2 setup). Run:

```bash
cd "D:/codeSpace/claudecode/stock_data/todos"
python scripts/cli.py goal list
python scripts/cli.py goal show example-goal
python scripts/cli.py task list --goal example-goal
python scripts/cli.py task show example-goal-T001
python scripts/cli.py task show example-goal-T002
```

Verify each returns exit 0 and prints sensible content. (We do NOT delete or restore the example goal in the smoke — that would change the user's actual DB state.)

- [ ] **Step 4: Commit**

```bash
cd "D:/codeSpace/claudecode/stock_data/todos"
git add README.md
git commit -m "README: add CRUD补全 examples (list/show/update/delete/restore)"
```

---

## Self-Review (run after writing the plan; fix inline)

After completing the plan above, do this checklist:

1. **Spec coverage.** Walk through each spec section:
   - §2 constraints — covered by Global Constraints ✓
   - §3 architecture — covered by File Structure in §1, §2-§5 of the plan ✓
   - §4 core surfaces — db.py archive/restore (Task 1) + sync_md.py archived (Task 2) + CLI (Tasks 3-4) + dashboard (Task 5) ✓
   - §5 compatibility — respected: no schema migration, no new deps, Item 5 `TODO_GOALS_DIR` reused ✓
   - §6 acceptance criteria — each AC maps to one or more tests across Tasks 1-5; AC #15 (end-to-end rebuild-timers cleanup of archived goal timers) is implicitly verified by Task 2's scheduler exclusion + Item 2's existing rebuild-timers tests
   - §7 test plan — every test class listed in §7 has corresponding test code in Tasks 1-5

2. **Placeholder scan.** Search the plan for "TBD", "TODO", "implement later", "similar to", "etc.". The matches found:
   - `--depends-on` parameter is left untouched in `task list` (out of scope per spec §5 non-goals) — explicitly called out, not a placeholder
   - `task list` excludes archived via post-filter instead of `db.list_tasks` supporting it natively — implementation choice, not a placeholder
   - No "fill in later" content anywhere

3. **Type / name consistency.**
   - `db.archive_goal` / `db.archive_task` / `db.restore_goal` / `db.restore_task` defined in Task 1, used in Tasks 3-4 ✓
   - `STATUS_LABELS["archived"]` defined in Task 2 (sync_md.py), used in Task 3's `goal list` / `goal show` ✓
   - `STATUS_LABELS` for tasks (`pending`/`in_progress`/`done`/`skipped`/`archived`) defined inline in Task 4 — consistent with dashboard's STATUS_LABELS at `dashboard/app.py:23-32` ✓
   - `_autosync_index_md()` called from 4 new sites (Task 3: goal update/delete/restore, Task 4: task delete/restore) — signature unchanged ✓

4. **Test count math.**
   - Task 1: 11 db tests
   - Task 2: 4 sync-md tests + 1 scheduler test = 5
   - Task 3: 17 goal CLI tests
   - Task 4: 12 task CLI tests
   - Task 5: 7 dashboard tests
   - Task 6: 0 (verification only)
   - **Total new tests: 52**
   - **Plus Item 5's 145 = 197, minus overlaps** (the new tests fully replace any older tests they touch, but no overlaps in scope) → expected total ~197 passed + 2 pre-existing failures = 199 collected.

5. **Risks called out:**
   - Task 3 step 4 depends on the actual structure of `run()`'s `goal` dispatch branch (whether it currently uses `_dispatch_goal` or inline). Step 4 gives both instructions and tells the implementer to inspect and adapt.
   - Task 5 step 6/7/8 depends on `base.html`'s Jinja block names. Step 8 says "if base.html uses different block names, adapt."

(Final test count: 145 prior + 52 new = 197 total, plus 2 pre-existing failures.)