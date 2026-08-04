"""Tests for scripts/sync_md.py — pure-function and file-I/O layers."""

from __future__ import annotations

import json
import pytest
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Import the module under test. We do this via sys.path manipulation so the
# test file matches the cli test layout (which uses subprocess, but the
# pure-function tests here import directly for speed).
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import sync_md  # noqa: E402

# For TestSyncMdCli, also import from tests.test_cli:
_sys = sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_cli import _init_db, run_cli  # type: ignore


# -------------------- TestRenderIndexMd --------------------

class TestRenderIndexMd:
    def test_empty_goals_renders_only_header(self):
        result = sync_md.render_index_md(
            goals=[],
            tasks_by_goal={},
            header_text="# My Header\n\nSome intro text.",
        )
        # Header preserved (trailing newline stripped), no headings.
        assert result.startswith("# My Header\n\nSome intro text.")
        assert "## 进行中" not in result
        assert "## 已暂停" not in result
        assert "## 已完成" not in result

    def test_single_active_goal(self):
        goals = [
            {"slug": "example", "name": "示例目标", "status": "active"},
        ]
        tasks_by_goal = {
            "example": [
                {"id": "example-T001", "status": "done"},
                {"id": "example-T002", "status": "pending"},
            ],
        }
        result = sync_md.render_index_md(goals, tasks_by_goal, header_text="")
        assert "## 进行中" in result
        assert "- [示例目标](example/goal.md) — 状态：进行中 — 完成率 50%" in result
        # Status appears in the line; the heading itself does too.
        assert result.count("进行中") >= 1

    def test_groups_in_order_active_paused_completed(self):
        goals = [
            {"slug": "comp-goal", "name": "C", "status": "completed"},
            {"slug": "active-goal", "name": "A", "status": "active"},
            {"slug": "paused-goal", "name": "P", "status": "paused"},
        ]
        result = sync_md.render_index_md(goals, {}, header_text="")
        # Heading order: active before paused before completed.
        active_pos = result.index("## 进行中")
        paused_pos = result.index("## 已暂停")
        completed_pos = result.index("## 已完成")
        assert active_pos < paused_pos < completed_pos

    def test_empty_group_section_omitted(self):
        goals = [
            {"slug": "active-only", "name": "A", "status": "active"},
        ]
        result = sync_md.render_index_md(goals, {}, header_text="")
        assert "## 进行中" in result
        assert "## 已暂停" not in result
        assert "## 已完成" not in result

    def test_completion_pct_with_zero_tasks(self):
        assert sync_md.compute_completion_pct([]) == 0

    def test_sort_within_group_by_slug(self):
        goals = [
            {"slug": "zebra", "name": "Z", "status": "active"},
            {"slug": "alpha", "name": "A", "status": "active"},
            {"slug": "mike", "name": "M", "status": "active"},
        ]
        result = sync_md.render_index_md(goals, {}, header_text="")
        # The three lines, in order, should reference alpha, mike, zebra.
        alpha_pos = result.index("[A](alpha/goal.md)")
        mike_pos = result.index("[M](mike/goal.md)")
        zebra_pos = result.index("[Z](zebra/goal.md)")
        assert alpha_pos < mike_pos < zebra_pos


# -------------------- TestSyncIndexMd --------------------

class TestSyncIndexMd:
    def _seed_db(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        goals: list[dict],
        tasks: list[dict],
    ) -> None:
        """Seed a minimal SQLite DB with the schema + given goals/tasks.

        Points `db.DB_PATH` at the temp DB (the module reads TODO_DB_PATH once
        at import time, so patching the attribute is what actually isolates a
        test — same pattern as tests/test_dashboard.py). Each goal's goal.md is
        created on disk.
        """
        import sqlite3

        import db

        db_path = tmp_path / "todos.db"
        schema_path = REPO_ROOT / "data" / "schema.sql"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(schema_path.read_text(encoding="utf-8"))
            for g in goals:
                conn.execute(
                    "INSERT INTO goals (slug, name, description, status, "
                    "total_tasks, completed_tasks, created_at, updated_at) "
                    "VALUES (?, ?, '', ?, 0, 0, '2026-08-04T00:00:00', "
                    "'2026-08-04T00:00:00')",
                    (g["slug"], g["name"], g["status"]),
                )
                (tmp_path / "goals" / g["slug"]).mkdir(parents=True, exist_ok=True)
                (tmp_path / "goals" / g["slug"] / "goal.md").write_text(
                    f"# {g['name']}\n", encoding="utf-8"
                )
            for t in tasks:
                conn.execute(
                    "INSERT INTO tasks (id, goal_slug, sequence, title, "
                    "description, estimated_hours, depends_on, status, "
                    "last_reminded_at, completed_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, '', '', 0.0, '[]', ?, NULL, NULL, "
                    "'2026-08-04T00:00:00', '2026-08-04T00:00:00')",
                    (t["id"], t["goal_slug"], t["sequence"], t["status"]),
                )
            conn.commit()
        finally:
            conn.close()
        monkeypatch.setattr(db, "DB_PATH", str(db_path))

    def test_creates_index_md_if_missing(self, tmp_path, monkeypatch):
        self._seed_db(tmp_path, monkeypatch,
                      goals=[{"slug": "foo", "name": "Foo", "status": "active"}],
                      tasks=[])
        result = sync_md.sync_index_md(tmp_path / "goals")
        assert result.path == tmp_path / "goals" / "index.md"
        assert (tmp_path / "goals" / "index.md").exists()
        content = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "## 进行中" in content
        assert "- [Foo](foo/goal.md)" in content

    def test_preserves_existing_header(self, tmp_path, monkeypatch):
        # Pre-create index.md with a custom header.
        goals_dir = tmp_path / "goals"
        goals_dir.mkdir(parents=True, exist_ok=True)
        (goals_dir / "index.md").write_text(
            "# 我的目标索引\n\n由 sync-md 自动维护。\n",
            encoding="utf-8",
        )
        self._seed_db(tmp_path, monkeypatch,
                      goals=[{"slug": "x", "name": "X", "status": "active"}],
                      tasks=[])
        result = sync_md.sync_index_md(goals_dir)
        content = (goals_dir / "index.md").read_text(encoding="utf-8")
        # Header bytes preserved.
        assert content.startswith("# 我的目标索引\n\n由 sync-md 自动维护。\n")
        assert result.header_preserved
        # Then the regenerated list follows.
        assert "## 进行中" in content
        assert "- [X](x/goal.md)" in content

    def test_overwrites_only_list_section(self, tmp_path, monkeypatch):
        goals_dir = tmp_path / "goals"
        goals_dir.mkdir(parents=True, exist_ok=True)
        # Pre-create with a stale list section that should be discarded.
        (goals_dir / "index.md").write_text(
            "# Header\n\n- [Stale](stale/goal.md) — bogus\n",
            encoding="utf-8",
        )
        self._seed_db(tmp_path, monkeypatch,
                      goals=[{"slug": "fresh", "name": "F", "status": "active"}],
                      tasks=[])
        sync_md.sync_index_md(goals_dir)
        content = (goals_dir / "index.md").read_text(encoding="utf-8")
        assert content.startswith("# Header\n")
        assert "Stale" not in content
        assert "[F](fresh/goal.md)" in content

    def test_warns_on_orphan_directory(self, tmp_path, monkeypatch):
        self._seed_db(tmp_path, monkeypatch,
                      goals=[{"slug": "real", "name": "R", "status": "active"}],
                      tasks=[])
        # Create an orphan goal dir (no DB row).
        (tmp_path / "goals" / "orphan").mkdir(parents=True, exist_ok=True)
        (tmp_path / "goals" / "orphan" / "goal.md").write_text(
            "# Orphan\n", encoding="utf-8"
        )
        result = sync_md.sync_index_md(tmp_path / "goals")
        assert any("orphan" in w for w in result.warnings)
        # The orphan goal is NOT in the index.
        content = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "[Orphan]" not in content

    def test_warns_when_goal_md_missing(self, tmp_path, monkeypatch):
        self._seed_db(tmp_path, monkeypatch,
                      goals=[{"slug": "missing-md", "name": "M", "status": "active"}],
                      tasks=[])
        # Delete the goal.md we just created in _seed_db.
        (tmp_path / "goals" / "missing-md" / "goal.md").unlink()
        result = sync_md.sync_index_md(tmp_path / "goals")
        assert any("missing-md" in w and "goal.md" in w for w in result.warnings)
        # But the link is still rendered.
        content = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "[M](missing-md/goal.md)" in content

    def test_warns_when_goal_dir_missing_entirely(self, tmp_path, monkeypatch):
        """I3: DB has a goal whose goals/<slug>/ directory is entirely absent
        → warning fires AND the link is still rendered in the index."""
        # Seed by adding only the DB row (no goals/ dir, no goal.md).
        import db as _db_mod

        db_path = tmp_path / "todos.db"
        schema_path = REPO_ROOT / "data" / "schema.sql"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(schema_path.read_text(encoding="utf-8"))
            conn.execute(
                "INSERT INTO goals (slug, name, description, status, "
                "total_tasks, completed_tasks, created_at, updated_at) "
                "VALUES (?, ?, '', ?, 0, 0, '2026-08-04T00:00:00', "
                "'2026-08-04T00:00:00')",
                ("ghost", "G", "active"),
            )
            conn.commit()
        finally:
            conn.close()
        monkeypatch.setattr(_db_mod, "DB_PATH", str(db_path))

        result = sync_md.sync_index_md(tmp_path / "goals")
        assert any("ghost" in w for w in result.warnings)
        # And the link is rendered anyway.
        content = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "[G](ghost/goal.md)" in content

    def test_idempotent_and_no_heading_accumulation(self, tmp_path, monkeypatch):
        """Spec §"Idempotency": re-running with no DB change is byte-identical.

        The generated `## <label>` headings sit *before* the first link line, so
        a naive header split would re-absorb them into the header and duplicate
        them on every sync.
        """
        import db

        goals_dir = tmp_path / "goals"
        self._seed_db(tmp_path, monkeypatch,
                      goals=[{"slug": "a", "name": "A", "status": "active"}],
                      tasks=[])
        sync_md.sync_index_md(goals_dir)
        first = (goals_dir / "index.md").read_text(encoding="utf-8")
        result = sync_md.sync_index_md(goals_dir)
        second = (goals_dir / "index.md").read_text(encoding="utf-8")
        assert second == first
        assert result.changed == []
        assert result.unchanged == ["a"]
        assert first.count("## 进行中") == 1

        # A status change must move the goal, not leave a stale heading behind.
        db.update_goal_status("a", "paused")
        sync_md.sync_index_md(goals_dir)
        moved = (goals_dir / "index.md").read_text(encoding="utf-8")
        assert "## 进行中" not in moved
        assert moved.count("## 已暂停") == 1

    def test_warns_on_unknown_status(self, tmp_path, monkeypatch):
        """M9: a goal with a status outside the three buckets must trigger a
        warning."""
        # Seed an active goal so the regular sync works, then push a row
        # directly into the DB with an unknown status.
        self._seed_db(
            tmp_path, monkeypatch,
            goals=[{"slug": "ok", "name": "OK", "status": "active"}],
            tasks=[],
        )
        conn = sqlite3.connect(str((tmp_path / "todos.db")))
        try:
            conn.execute(
                "INSERT INTO goals (slug, name, description, status, "
                "total_tasks, completed_tasks, created_at, updated_at) "
                "VALUES (?, ?, '', ?, 0, 0, '2026-08-04T00:00:00', "
                "'2026-08-04T00:00:00')",
                ("weird", "W", "frobnicated"),
            )
            conn.commit()
        finally:
            conn.close()
        result = sync_md.sync_index_md(tmp_path / "goals")
        assert any("frobnicated" in w and "unknown" in w for w in result.warnings)
        # It must not appear in the rendered index.
        content = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "[W]" not in content

    def test_preserves_user_header_above_generated_sections(
        self, tmp_path, monkeypatch
    ):
        goals_dir = tmp_path / "goals"
        self._seed_db(tmp_path, monkeypatch,
                      goals=[{"slug": "a", "name": "A", "status": "active"}],
                      tasks=[])
        sync_md.sync_index_md(goals_dir)
        # User prepends a header to the already-generated file.
        generated = (goals_dir / "index.md").read_text(encoding="utf-8")
        (goals_dir / "index.md").write_text(
            "# 目标索引\n\n手写说明。\n\n" + generated, encoding="utf-8"
        )
        sync_md.sync_index_md(goals_dir)
        content = (goals_dir / "index.md").read_text(encoding="utf-8")
        assert content.startswith("# 目标索引\n\n手写说明。\n")
        assert content.count("## 进行中") == 1

    def test_changed_vs_added_classification(self, tmp_path, monkeypatch):
        """I4: SyncResult classifies slugs into added (new) vs changed
        (pre-existing, rendered line differs).
        """
        import db

        goals_dir = tmp_path / "goals"
        self._seed_db(
            tmp_path, monkeypatch,
            goals=[{"slug": "a", "name": "A", "status": "active"}],
            tasks=[],
        )
        sync_md.sync_index_md(goals_dir)
        # 'a' appears in both old and new (no change).
        r1 = sync_md.sync_index_md(goals_dir)
        assert r1.added == []
        assert r1.changed == []
        # Flip status: 'a' is now in old (active) and new (paused) — that's
        # a change, not an add.
        db.update_goal_status("a", "paused")
        r2 = sync_md.sync_index_md(goals_dir)
        assert "a" in r2.changed
        assert "a" not in r2.added
        # Now create a brand-new goal. It should land in `added`, not `changed`.
        conn = sqlite3.connect(str((tmp_path / "todos.db")))
        try:
            conn.execute(
                "INSERT INTO goals (slug, name, description, status, "
                "total_tasks, completed_tasks, created_at, updated_at) "
                "VALUES (?, ?, '', ?, 0, 0, '2026-08-04T00:00:00', "
                "'2026-08-04T00:00:00')",
                ("b", "B", "paused"),
            )
            conn.commit()
        finally:
            conn.close()
        (goals_dir / "b").mkdir(parents=True, exist_ok=True)
        (goals_dir / "b" / "goal.md").write_text("# B\n", encoding="utf-8")
        r3 = sync_md.sync_index_md(goals_dir)
        assert "b" in r3.added
        assert "b" not in r3.changed
        # 'a' is unchanged now (the file at r2 already had it as paused).
        assert "a" in r3.unchanged
        assert "a" not in r3.added
        assert "a" not in r3.changed


# -------------------- TestSyncMdCli --------------------

class TestSyncMdCli:
    def _seed(self, tmp_path: Path, goals: list[dict], tasks: list[dict]) -> Path:
        """Seed an isolated DB and return its path.

        Goals are also created on disk under tmp_path/goals/<slug>/goal.md
        so sync_index_md can verify them as non-orphan.
        """
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            for g in goals:
                conn.execute(
                    "INSERT INTO goals (slug, name, description, status, "
                    "total_tasks, completed_tasks, created_at, updated_at) "
                    "VALUES (?, ?, '', ?, 0, 0, '2026-08-04T00:00:00', "
                    "'2026-08-04T00:00:00')",
                    (g["slug"], g["name"], g["status"]),
                )
                (tmp_path / "goals" / g["slug"]).mkdir(parents=True, exist_ok=True)
                (tmp_path / "goals" / g["slug"] / "goal.md").write_text(
                    f"# {g['name']}\n", encoding="utf-8"
                )
            for t in tasks:
                conn.execute(
                    "INSERT INTO tasks (id, goal_slug, sequence, title, "
                    "description, estimated_hours, depends_on, status, "
                    "last_reminded_at, completed_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, '', 0.0, '[]', ?, NULL, NULL, "
                    "'2026-08-04T00:00:00', '2026-08-04T00:00:00')",
                    (t["id"], t["goal_slug"], t["sequence"], f"Task {t['id']}", t["status"]),
                )
            conn.commit()
        return db_path

    def test_human_output_default(self, tmp_path):
        db_path = self._seed(
            tmp_path,
            goals=[{"slug": "foo", "name": "Foo", "status": "active"}],
            tasks=[],
        )
        result = run_cli(["sync-md"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "Synced" in result.stdout
        assert "active=1" in result.stdout
        # File was written under tmp_path/goals/.
        assert (tmp_path / "goals" / "index.md").exists()

    def test_spec_5_2_slug_column_width(self, tmp_path):
        """Spec §5.2: slug block (marker+slug) is right-padded to 16 chars.

        For slug 'goal-a' (6 chars), the printed block is '<marker>goal-a' +
        9 trailing spaces = 16 chars total. Acceptance: the substring
        'goal-a          ' (goal-a + 10 spaces) appears in stdout, preceded
        by either ' ' (unchanged) or '+' (changed) marker.
        """
        db_path = self._seed(
            tmp_path,
            goals=[{"slug": "goal-a", "name": "G", "status": "active"}],
            tasks=[],
        )
        # First run creates the file (marker '+'). Second run is unchanged
        # (marker ' '). Either way, the column width must be 16.
        run_cli(["sync-md"], db_path=db_path, cwd=tmp_path)
        result = run_cli(["sync-md"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        # Block = space-marker + 'goal-a' + 9 trailing spaces = 16 chars.
        assert " goal-a          " in result.stdout

    def test_json_output_shape(self, tmp_path):
        db_path = self._seed(
            tmp_path,
            goals=[
                {"slug": "foo", "name": "Foo", "status": "active"},
                {"slug": "bar", "name": "Bar", "status": "paused"},
            ],
            tasks=[],
        )
        result = run_cli(["sync-md", "--json"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert set(data.keys()) >= {
            "path", "synced_count", "by_status", "changed", "unchanged",
            "warnings", "header_preserved",
        }
        assert data["synced_count"] == 2
        assert data["by_status"] == {"active": 1, "paused": 1, "completed": 0}

    def test_db_uninitialized_exits_2(self, tmp_path):
        # Don't seed; use an empty DB with no schema_version.
        empty_db = tmp_path / "empty.db"
        empty_db.touch()
        result = run_cli(["sync-md"], db_path=empty_db, cwd=tmp_path)
        assert result.returncode == 2
        assert "Run `python scripts/db.py init` first" in result.stderr

    def test_warnings_on_stderr(self, tmp_path):
        db_path = self._seed(
            tmp_path,
            goals=[{"slug": "real", "name": "R", "status": "active"}],
            tasks=[],
        )
        # Add an orphan dir.
        (tmp_path / "goals" / "orphan").mkdir(parents=True, exist_ok=True)
        (tmp_path / "goals" / "orphan" / "goal.md").write_text(
            "# O\n", encoding="utf-8"
        )
        result = run_cli(["sync-md"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0  # warnings don't block
        assert "warning" in result.stderr.lower()
        assert "orphan" in result.stderr

    def test_warnings_dont_block_exit_0(self, tmp_path):
        db_path = self._seed(
            tmp_path,
            goals=[{"slug": "ok", "name": "OK", "status": "active"}],
            tasks=[],
        )
        (tmp_path / "goals" / "stray").mkdir(parents=True, exist_ok=True)
        (tmp_path / "goals" / "stray" / "goal.md").write_text("# S\n", encoding="utf-8")
        result = run_cli(["sync-md"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        # Index.md still written.
        assert (tmp_path / "goals" / "index.md").exists()

    def test_json_flag_before_subcommand(self, tmp_path):
        """Regression for I2: --json BEFORE the subcommand must take effect.

        Previously the sync-md subparser's --json defaulted to False, which
        overrode the top-level True when dispatch evaluated getattr(parsed,
        'json', False). Now SUPPRESS keeps the subparser's --json absent
        unless the flag is actually passed after the subcommand.
        """
        db_path = self._seed(
            tmp_path, goals=[{"slug": "x", "name": "X", "status": "active"}],
            tasks=[],
        )
        result = run_cli(["--json", "sync-md"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert "synced_count" in data

    def test_path_uses_forward_slashes(self, tmp_path):
        """M10: spec §5.3 shows 'goals/index.md' with forward slashes;
        Path(...).as_posix() ensures no backslashes regardless of OS."""
        db_path = self._seed(
            tmp_path,
            goals=[{"slug": "foo", "name": "Foo", "status": "active"}],
            tasks=[],
        )
        result = run_cli(["sync-md", "--json"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        # No backslashes anywhere in the path string.
        assert "\\" not in data["path"]
        # Path ends in 'goals/index.md' (the forward-slashed suffix).
        assert data["path"].endswith("goals/index.md")
        # Human output also uses forward slashes.
        hr = run_cli(["sync-md"], db_path=db_path, cwd=tmp_path)
        synced_line = hr.stdout.split("\n")[0]
        assert "goals/index.md" in synced_line
        assert "\\" not in synced_line

    def test_tilde_marker_for_status_change(self, tmp_path):
        """I4: a pre-existing goal whose rendered line differs gets '~' marker,
        not '+'."""
        db_path = self._seed(
            tmp_path,
            goals=[{"slug": "a", "name": "A", "status": "active"}],
            tasks=[],
        )
        # First run creates the file: 'a' is 'added' → '+'.
        run_cli(["sync-md"], db_path=db_path, cwd=tmp_path)
        # Flip status directly in the DB so the next sync sees a change.
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE goals SET status='paused' WHERE slug='a'"
            )
            conn.commit()
        result = run_cli(["sync-md"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        # 'a' must get the '~' marker (line differs from prior file).
        assert "- ~a" in result.stdout
        # And not the '+' marker.
        assert "- +a" not in result.stdout

    def test_idempotent_second_run(self, tmp_path):
        db_path = self._seed(
            tmp_path,
            goals=[{"slug": "x", "name": "X", "status": "active"}],
            tasks=[{"id": "x-T001", "goal_slug": "x", "sequence": 1, "status": "done"}],
        )
        # First run.
        r1 = run_cli(["sync-md"], db_path=db_path, cwd=tmp_path)
        assert r1.returncode == 0
        first = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        # Second run.
        r2 = run_cli(["sync-md"], db_path=db_path, cwd=tmp_path)
        assert r2.returncode == 0
        second = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert first == second


# -------------------- TestAutosyncIntegration --------------------

class TestAutosyncIntegration:
    def _seed_db(self, tmp_path: Path) -> Path:
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        return db_path

    def test_goal_add_triggers_sync(self, tmp_path):
        db_path = self._seed_db(tmp_path)
        result = run_cli(
            ["goal", "add", "newgoal", "New Goal", "--description", ""],
            db_path=db_path, cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        # goals/index.md must exist and contain newgoal.
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "## 进行中" in index
        assert "[New Goal](newgoal/goal.md)" in index

    def test_task_add_triggers_sync(self, tmp_path):
        db_path = self._seed_db(tmp_path)
        # First add a goal so task add succeeds.
        run_cli(
            ["goal", "add", "tg", "TaskGoal"],
            db_path=db_path, cwd=tmp_path,
        )
        # Add a task — should keep tg in the index.
        result = run_cli(
            ["task", "add", "tg-T001", "tg", "1", "First task", "--hours", "1.0"],
            db_path=db_path, cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "[TaskGoal](tg/goal.md)" in index

    def test_task_update_to_done_updates_pct(self, tmp_path):
        db_path = self._seed_db(tmp_path)
        run_cli(["goal", "add", "g", "G"], db_path=db_path, cwd=tmp_path)
        run_cli(
            ["task", "add", "g-T001", "g", "1", "T1", "--hours", "1.0"],
            db_path=db_path, cwd=tmp_path,
        )
        run_cli(
            ["task", "add", "g-T002", "g", "2", "T2", "--hours", "1.0"],
            db_path=db_path, cwd=tmp_path,
        )
        # Mark T001 done.
        result = run_cli(
            ["task", "update", "g-T001", "done"],
            db_path=db_path, cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        # 1 of 2 done = 50%
        assert "完成率 50%" in index

    def test_sync_failure_does_not_block_caller(self, tmp_path):
        """A sync-md failure inside _autosync_index_md must not propagate.

        Forces sync_index_md to raise by pre-placing a regular file at
        the goals/ path: sync_index_md does `goals_root.mkdir(...)` first,
        which raises FileExistsError (an OSError subclass) when the path
        is a file. _autosync_index_md must catch the exception, log a
        warning to stderr, and let the subcommand return 0.
        """
        db_path = self._seed_db(tmp_path)
        # Force sync_index_md to fail: place a regular file where it
        # expects a directory.
        (tmp_path / "goals").write_text("not a directory", encoding="utf-8")

        result = run_cli(
            ["goal", "add", "x", "X"],
            db_path=db_path, cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert "Goal 'x' created." in result.stdout
        assert "warning: sync-md failed:" in result.stderr

    def test_cwd_none_does_not_corrupt_tracked_index(self, tmp_path):
        """Regression for C1: when run_cli is called without cwd= (its
        default), the auto-trigger must NOT overwrite the tracked
        REPO_ROOT/goals/index.md."""
        # Capture the committed version of goals/index.md via git.
        tracked = REPO_ROOT / "goals" / "index.md"
        before = subprocess.run(
            ["git", "show", "HEAD:goals/index.md"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=True,
        ).stdout

        db_path = self._seed_db(tmp_path)
        # Pass cwd=None explicitly (default) to test the legacy code path
        # that previously corrupted the tracked file.
        result = run_cli(["goal", "add", "x", "X"], db_path=db_path)
        assert result.returncode == 0, result.stderr
        assert "Goal 'x' created." in result.stdout

        # The tracked file must still match the committed version.
        after = subprocess.run(
            ["git", "show", "HEAD:goals/index.md"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=True,
        ).stdout
        assert after == before
        # And the on-disk file must not have been modified (Path(tmpfile)
        # was used; tracked file is unmodified).
        if tracked.exists():
            assert tracked.read_text(encoding="utf-8") == before


# -------------------- TestArchivedGoalInSync --------------------

class TestArchivedGoalInSync:
    """Archived goals must be hidden from goals/index.md rendering.

    These tests use the schema.sql + ALTER TABLE pattern (see
    tests/test_db.py:15-18) because db._init_schema() does not exist.
    """

    def _seed_db(self, tmp_path, monkeypatch, goals_spec):
        """Seed an isolated DB. goals_spec is list of dicts with slug/name/status.

        Each goal also creates a stub goals/<slug>/goal.md.
        """
        import db
        schema_path = REPO_ROOT / "data" / "schema.sql"
        db_path = tmp_path / "todos.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(schema_path.read_text(encoding="utf-8"))
            conn.execute("ALTER TABLE tasks ADD COLUMN started_at TEXT")
            for g in goals_spec:
                status = g.get("status", "active")
                conn.execute(
                    "INSERT INTO goals (slug, name, description, status, "
                    "total_tasks, completed_tasks, created_at, updated_at) "
                    "VALUES (?, ?, '', ?, 0, 0, '2026-08-04T00:00:00', "
                    "'2026-08-04T00:00:00')",
                    (g["slug"], g["name"], status),
                )
                (tmp_path / "goals" / g["slug"]).mkdir(
                    parents=True, exist_ok=True
                )
                (tmp_path / "goals" / g["slug"] / "goal.md").write_text(
                    f"# {g['name']}\n", encoding="utf-8"
                )
            conn.commit()
        finally:
            conn.close()
        monkeypatch.setattr(db, "DB_PATH", str(db_path))
        return db_path

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
        # synced_count includes archived (it IS a known DB row) but by_status
        # does not bucket archived.
        assert result.synced_count == 2
        assert "archived" not in result.by_status
        assert result.by_status["active"] == 1

    def test_restore_makes_goal_reappear(self, tmp_path, monkeypatch):
        import db
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
        import db
        schema_path = REPO_ROOT / "data" / "schema.sql"
        db_path = tmp_path / "todos.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.executescript(schema_path.read_text(encoding="utf-8"))
            conn.execute("ALTER TABLE tasks ADD COLUMN started_at TEXT")
            conn.execute(
                "INSERT INTO goals (slug, name, description, status, "
                "total_tasks, completed_tasks, created_at, updated_at) "
                "VALUES (?, ?, '', ?, 0, 0, '2026-08-04T00:00:00', "
                "'2026-08-04T00:00:00')",
                ("archived-ghost", "G", "archived"),
            )
            conn.commit()
        finally:
            conn.close()
        monkeypatch.setattr(db, "DB_PATH", str(db_path))
        # Note: NO goals/archived-ghost/ directory or goal.md created.
        result = sync_md.sync_index_md(tmp_path / "goals")
        assert not any("archived-ghost" in w for w in result.warnings)

