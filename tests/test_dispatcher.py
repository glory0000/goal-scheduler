"""Tests for scripts/dispatcher.py — expand-request detection."""

import sys
from pathlib import Path

# Make scripts/ importable (consistent with other tests in this repo)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dispatcher  # noqa: E402


class TestIsExpandRequest:
    def test_T001_展开(self):
        is_exp, tid = dispatcher.is_expand_request("T001 展开")
        assert is_exp is True
        assert tid == "T001"

    def test_展开_T001_reverse_order(self):
        is_exp, tid = dispatcher.is_expand_request("展开 T001")
        assert is_exp is True
        assert tid == "T001"

    def test_详细_怎么做_synonyms(self):
        for trigger in ["详细", "怎么做"]:
            is_exp, tid = dispatcher.is_expand_request(f"T002 {trigger}")
            assert is_exp is True
            assert tid == "T002"

    def test_full_task_id_accepted(self):
        is_exp, tid = dispatcher.is_expand_request("remotion-finance-T001 展开")
        assert is_exp is True
        assert tid == "remotion-finance-T001"

    def test_not_an_expand_request(self):
        is_exp, tid = dispatcher.is_expand_request("T001 完成了")
        assert is_exp is False
        assert tid is None

    def test_unrelated_text(self):
        is_exp, tid = dispatcher.is_expand_request("今天天气怎么样")
        assert is_exp is False
        assert tid is None
