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

