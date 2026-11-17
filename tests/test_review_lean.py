#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""review_lean 测试：数据行构造、prompt 组装、verify 加载、dry-run 集成。

不调用任何真实模型（opencode）；运行：
  python -m unittest discover -s tests -v
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "llm"))

import review_lean  # noqa: E402

EXAMPLES = REPO_ROOT / "examples"


def stock(**over):
    """verify 记录模板，与示例数据同构。"""
    base = {
        "code": "600001", "name": "示例A", "data_date": "2026-11-06",
        "close": 10.5, "prev_close": 10.0, "ma233": 10.0, "dist_pct": 5.0,
        "holding_now": True, "prev_entry_date": "2026-10-01",
        "prev_entry_price": 10.0, "exec_date": "2026-10-01", "exec_price": 10.0,
        "unreal": 5.0, "stop_pending": False, "status": "ok",
        "prev_class": "holding_recent",
    }
    base.update(over)
    return base


class StockDataLineTest(unittest.TestCase):
    def test_exec_price_preferred(self):
        """v1.1：有 exec_price 时入场口径必须是实际成交价。"""
        line = review_lean.stock_data_line(stock(exec_price=12.0, exec_date="2026-11-06"))
        self.assertIn("入场 2026-11-06 @ 12.00", line)

    def test_fallback_to_signal_price(self):
        """无 exec_price 时回退 prev_entry_price。"""
        line = review_lean.stock_data_line(
            stock(exec_price=None, exec_date=None, prev_entry_price=9.5)
        )
        self.assertIn("入场 2026-10-01 @ 9.50", line)

    def test_stop_pending_label(self):
        self.assertIn("止损待触发 是", review_lean.stock_data_line(stock(stop_pending=True)))
        self.assertIn("止损待触发 否", review_lean.stock_data_line(stock(stop_pending=False)))


class BuildPromptTest(unittest.TestCase):
    def test_prompt_contains_hard_rules_and_data(self):
        prompt = review_lean.build_prompt([stock()], "20261106")
        # 硬规则在场
        self.assertIn("不给出买入、卖出", prompt)
        self.assertIn("不编造", prompt)
        # 日期与数据行在场
        self.assertIn("20261106", prompt)
        self.assertIn("600001 示例A", prompt)
        self.assertIn("MA233 10.00", prompt)
        # 输出格式要求四行结构
        self.assertIn("趋势位置", prompt)
        self.assertIn("一句话结论", prompt)

    def test_prompt_batch_order(self):
        stocks = [stock(code="600003"), stock(code="600001")]
        prompt = review_lean.build_prompt(stocks, "20261106")
        self.assertLess(prompt.index("600003"), prompt.index("600001"))


class LoadVerifyTest(unittest.TestCase):
    def test_loads_example(self):
        rows = review_lean.load_verify(EXAMPLES / "verify_example.json")
        self.assertEqual(len(rows), 3)

    def test_rejects_non_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "bad.json"
            p.write_text('{"not": "a list"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                review_lean.load_verify(str(p))


class DryRunIntegrationTest(unittest.TestCase):
    def test_dry_run_exits_zero_without_model(self):
        """--dry-run 只打印 prompt，绝不调用 opencode。"""
        r = subprocess.run(
            [sys.executable, str(REPO_ROOT / "llm" / "review_lean.py"),
             "--verify", str(EXAMPLES / "verify_example.json"),
             "--date", "20261106", "--stocks", "600001,600002", "--dry-run"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("batch 1 prompt", r.stdout)
        self.assertIn("600001 示例A", r.stdout)
        self.assertIn("600002 示例B", r.stdout)

    def test_missing_code_fails_fast(self):
        r = subprocess.run(
            [sys.executable, str(REPO_ROOT / "llm" / "review_lean.py"),
             "--verify", str(EXAMPLES / "verify_example.json"),
             "--date", "20261106", "--stocks", "999999", "--dry-run"],
            capture_output=True, text=True, timeout=60,
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("999999", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
