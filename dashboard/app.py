#!/usr/bin/env python3
"""Read-only web dashboard for the todo scheduler."""

import os
import sys
from pathlib import Path

from flask import Flask, Response

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import db


def create_app() -> Flask:
    flask_app = Flask(__name__)

    @flask_app.get("/health")
    def health() -> Response:
        return Response("ok", mimetype="text/plain")

    return flask_app


def validate_database(path: str | os.PathLike | None = None) -> None:
    db_path = Path(path if path is not None else db.DB_PATH)
    if not db_path.is_file():
        raise FileNotFoundError(f"Todo database not found: {db_path}")


def main() -> int:
    try:
        validate_database()
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        app.run(host="0.0.0.0", port=5000, debug=False)
    except OSError as exc:
        print(f"Error: unable to start dashboard on 0.0.0.0:5000: {exc}", file=sys.stderr)
        return 1
    return 0


app = create_app()


if __name__ == "__main__":
    raise SystemExit(main())
