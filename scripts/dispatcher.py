"""Dispatcher helpers for the cc-connect Claude session.

The cc-connect Claude session is the one that receives user replies
("T001 完成了", "T001 跳过", "T001 展开", etc.). This module provides
pure-function helpers the Claude can call via Bash to decide what to do.

Helpers:
    is_expand_request(text) -> (bool, task_id | None)
        True if `text` is a request to expand a task's howto. Recognised
        trigger words (closed set): 展开 / 详细 / 怎么做.
"""

from __future__ import annotations

import re

# Short or full task-id, then a trigger word (in either order, any
# whitespace, optional trailing/leading whitespace).
_TRIGGERS = "展开|详细|怎么做"
_TASK_ID_SHORT = r"T\d{3}"
_TASK_ID_FULL = r"[a-z0-9-]+-T\d{3}"
_TASK_ID = f"(?:{_TASK_ID_FULL}|{_TASK_ID_SHORT})"

_PATTERN = re.compile(
    rf"^\s*(?:(?P<id1>{_TASK_ID})\s+(?P<trig1>{_TRIGGERS})"
    rf"|(?P<trig2>{_TRIGGERS})\s+(?P<id2>{_TASK_ID}))\s*$"
)


def is_expand_request(text: str) -> tuple[bool, str | None]:
    """Return (True, task_id) if `text` matches the expand-request grammar.

    `task_id` is the captured group as-is (short like "T001" or full
    like "remotion-finance-T001"). Returns (False, None) otherwise.

    The grammar is intentionally closed: only the 3 trigger words above
    and a 3-digit T-suffix task id (with optional goal prefix). Anything
    else returns False.
    """
    if not text:
        return False, None
    m = _PATTERN.match(text)
    if not m:
        return False, None
    return True, m.group("id1") or m.group("id2")