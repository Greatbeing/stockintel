#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_verify（contrib 数据层参考实现）测试：纯计算部分，不联网。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "contrib"))

import fetch_verify  # noqa: E402


def series(closes, start="2025-01-01"):
    """给定收盘价列表，生成逐日升序 (date, close)。"""
    from datetime import date, timedelta

    d0 = date(2025, 1, 1)
    return [((d0 + timedelta(days=i)).isoformat(), c) for i, c in enumerate(closes)]


def full_history(last_close=10.5, prev_close=10.0, n=240):
    """构造 n 根历史：前段 10.0 平走，倒数第二根 prev_close，最后一根 last_close。"""
    closes = [10.0] * n
    closes[-2] = prev_close
    closes[-1] = last_close
    return closes


class ComputeRowsTest(unittest.TestCase):
    # 240 根里最后一根 10.5、倒数第二根 10.0，其余 10.0 → MA233 取最近 233 根，
    # 含 10.5 在内只有 1 根偏离 10.0：MA233 = (232*10.0 + 10.5)/233 ≈ 10.0021
    def test_basic_row(self):
        rows = fetch_verify.compute_rows({"600001": series(full_history())})
        row = rows[0]
        self.assertEqual(row["code"], "600001")
        self.assertEqual(row["close"], 10.5)
        self.assertEqual(row["prev_close"], 10.0)
        expected_ma = round((232 * 10.0 + 10.5) / 233, 4)
        self.assertEqual(row["ma233"], expected_ma)
        expected_dist = round((10.5 / expected_ma - 1.0) * 100.0, 2)
        self.assertEqual(row["dist_pct"], expected_dist)
        # 骨架字段：行情里没有的绝不编造
        self.assertIs(row["holding_now"], False)
        self.assertIsNone(row["unreal"])
        self.assertEqual(row["status"], "watch")

    def test_flat_ma233(self):
        """偏离价落在最近 233 根窗口之外时，MA233 精确等于 10.0。"""
        closes = [10.0] * 240
        closes[0] = 10.5  # 240 根取末 233 根，index 0 被排除
        rows = fetch_verify.compute_rows({"600001": series(closes)})
        self.assertEqual(rows[0]["ma233"], 10.0)
        self.assertEqual(rows[0]["close"], 10.0)
        self.assertEqual(rows[0]["dist_pct"], 0.0)

    def test_name_and_portfolio_merge(self):
        pf = {"600001": {"holding_now": True, "exec_price": 10.0,
                         "exec_date": "2026-10-01", "unreal": 5.0,
                         "stop_pending": False, "prev_class": "holding_recent"}}
        rows = fetch_verify.compute_rows(
            {"600001": series(full_history())}, names={"600001": "示例A"}, portfolio=pf
        )
        row = rows[0]
        self.assertEqual(row["name"], "示例A")
        self.assertIs(row["holding_now"], True)
        self.assertEqual(row["exec_price"], 10.0)
        self.assertEqual(row["unreal"], 5.0)
        # 未提供的字段保持骨架默认，不被 portfolio 覆盖丢掉
        self.assertIsNone(row["prev_entry_price"])

    def test_insufficient_history_raises(self):
        with self.assertRaises(fetch_verify.FetchError):
            fetch_verify.compute_rows({"600001": series(full_history(n=100))})


class CheckDateTest(unittest.TestCase):
    def _rows(self, d="2026-11-17"):
        return [{"code": "600001", "data_date": d}, {"code": "600002", "data_date": d}]

    def test_match_passes(self):
        self.assertEqual(fetch_verify.check_date(self._rows(), "20261117"), "2026-11-17")

    def test_mismatch_raises(self):
        with self.assertRaises(fetch_verify.FetchError):
            fetch_verify.check_date(self._rows(), "20261118")

    def test_inconsistent_dates_raise(self):
        rows = self._rows()
        rows[1]["data_date"] = "2026-11-16"
        with self.assertRaises(fetch_verify.FetchError):
            fetch_verify.check_date(rows, None)

    def test_none_date_returns_actual(self):
        self.assertEqual(fetch_verify.check_date(self._rows(), None), "2026-11-17")


class OutputShapeTest(unittest.TestCase):
    def test_output_matches_build_report_schema(self):
        """compute_rows 的输出必须能被主流程 load_verify 接受（字段齐全）。"""
        rows = fetch_verify.compute_rows({"600001": series(full_history())})
        required = {"code", "name", "data_date", "close", "ma233", "dist_pct", "holding_now"}
        self.assertTrue(required.issubset(rows[0].keys()))
        # dist_pct 能被 build_report 的口径复现
        row = rows[0]
        calc = (row["close"] / row["ma233"] - 1.0) * 100.0
        self.assertLess(abs(calc - row["dist_pct"]), 0.02)


if __name__ == "__main__":
    unittest.main(verbosity=2)
