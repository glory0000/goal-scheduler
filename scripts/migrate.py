#!/usr/bin/env python3
"""Forward-only SQLite migration runner for data/todos.db.

Subcommands:
  init      Bootstrap DB and stamp schema_version=1.
  upgrade   Apply pending migrations from TODO_MIGRATIONS_DIR.

Env vars:
  TODO_DB_PATH          SQLite database file (default: data/todos.db).
  TODO_MIGRATIONS_DIR   Directory holding NNN_*.sql files
                        (default: <repo>/migrations/).
"""

import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _get_db_path() -> Path:
    return Path(os.environ.get("TODO_DB_PATH", "data/todos.db"))


def _get_migrations_dir() -> Path:
    custom = os.environ.get("TODO_MIGRATIONS_DIR")
    if custom:
        return Path(custom)
    return Path(__file__).resolve().parent.parent / "migrations"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def cmd_init() -> int:
    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if _table_exists(conn, "schema_version"):
            current = conn.execute("SELECT version FROM schema_version").fetchone()[0]
            print(f"DB already initialized at version {current}")
            return 0

        if not _table_exists(conn, "goals"):
            schema_path = Path(__file__).resolve().parent.parent / "data" / "schema.sql"
            conn.executescript(schema_path.read_text())

        conn.executescript(
            "CREATE TABLE schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  applied_at TEXT NOT NULL"
            ")"
        )
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (1, ?)",
            (_now_iso(),),
        )
        conn.commit()
        print("DB initialized at version 1")
        return 0
    finally:
        conn.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Forward-only SQLite migration runner.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Bootstrap DB and stamp schema_version=1.")
    sub.add_parser("upgrade", help="Apply pending migrations from the migrations dir.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return cmd_init()
    parser.error(f"unknown command: {args.command}")
    return 2  # unreachable


if __name__ == "__main__":
    sys.exit(main())
