"""Display helpers for the todo scheduler."""

from datetime import datetime

from db import now_iso


def format_elapsed(started_at: str | None, completed_at: str | None = None) -> str:
    """Render a compact elapsed-time string.

    Returns "—" when started_at is None. Branches by magnitude:
    < 60s -> Ns, < 1h -> Xm Ys, < 24h -> Xh Ym, >= 24h -> Xd Yh.
    Raises ValueError on unparseable timestamps.
    """
    if started_at is None:
        return "—"
    end = completed_at if completed_at is not None else now_iso()
    start_dt = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%S")
    end_dt = datetime.strptime(end, "%Y-%m-%dT%H:%M:%S")
    seconds = max(0, int((end_dt - start_dt).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"
