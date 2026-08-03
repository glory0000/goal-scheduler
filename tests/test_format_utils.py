"""Unit tests for scripts/format_utils.py. Pure functions, no DB."""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from format_utils import format_elapsed


def test_format_elapsed_none_returns_dash():
    assert format_elapsed(None) == "—"


def test_format_elapsed_seconds_only():
    start = "2026-08-04T07:30:00"
    end = "2026-08-04T07:30:45"
    assert format_elapsed(start, completed_at=end) == "45s"


def test_format_elapsed_under_one_hour():
    start = "2026-08-04T07:00:00"
    end = "2026-08-04T07:59:59"
    assert format_elapsed(start, completed_at=end) == "59m 59s"


def test_format_elapsed_one_minute_five_seconds():
    start = "2026-08-04T07:00:00"
    end = "2026-08-04T07:01:05"
    assert format_elapsed(start, completed_at=end) == "1m 5s"


def test_format_elapsed_under_one_day():
    start = "2026-08-04T07:00:00"
    end = "2026-08-04T08:23:00"
    assert format_elapsed(start, completed_at=end) == "1h 23m"


def test_format_elapsed_multi_day():
    start = "2026-08-01T07:00:00"
    end = "2026-08-03T12:00:00"
    assert format_elapsed(start, completed_at=end) == "2d 5h"


def test_format_elapsed_malformed_raises_value_error():
    with pytest.raises(ValueError):
        format_elapsed("not-a-timestamp")
