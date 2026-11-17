#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stockintel 测试：日报组装器 + review 交叉核对正则。

零第三方依赖，仅用标准库 unittest。运行：
  python -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "report"))

import build_report  # noqa: E402

EXAMPLES = REPO_ROOT / "examples"


def make_verify():
    """与 examples/verify_example.json 等价的内存数据。"""
    return json.loads((EXAMPLES / "verify_example.json").read_text(encoding="utf-8"))


class _CfgMixin:
    """构造临时 build_report 配置的公共助手。"""

    CFG_BASE = {
        "project": "stockintel",
        "version": "M1",
        "date": "20261106",
        "model": {"name": "opencode/deepseek-v4.1-flash-free", "used_in_m1": False},
    }

    def cfg(self, tmp, date="20261106", verify="verify_example.json", output_name="out.md"):
        cfg = dict(self.CFG_BASE)
        cfg["date"] = date
        cfg["quant_root"] = str(EXAMPLES)
        cfg["inputs"] = {"verify": verify, "review": "review_example.md",
                         "buy_signals": "buy_signals_example.csv"}
        cfg["output"] = {"report": "%s/%s" % (tmp, output_name)}
        return cfg


class ConfigTest(_CfgMixin, unittest.TestCase):
    def test_load_example_config(self):
        cfg = build_report.load_config(EXAMPLES / "config.yaml")
        self.assertEqual(build_report.require(cfg, "date", str), "20261106")
        self.assertEqual(build_report.require(cfg, "inputs.verify", str),
                         "verify_example.json")

    def test_missing_config_raises(self):
        with self.assertRaises(build_report.BuildError):
            build_report.load_config(EXAMPLES / "no_such_config.yaml")

    def test_date_mismatch_raises(self):
        # verify 内 data_date 与期望日期不一致 → load_verify 必须停止
        with self.assertRaises(build_report.BuildError):
            build_report.load_verify(EXAMPLES / "verify_example.json", "2026-12-01")

    def test_date_match_passes(self):
        rows = build_report.load_verify(EXAMPLES / "verify_example.json", "2026-11-06")
        self.assertEqual(len(rows), 3)


class ReviewFactsTest(unittest.TestCase):
    OLD_BLOCK = """【600001】
- 趋势位置：收盘 10.50，位于 MA233（10.00）上方 5.00%，单日 +5.00%。
"""

    NEW_BLOCK = """【600001】
- 趋势位置：收盘 10.50，位于 MA233 10.00 上方 5.00%，单日 +5.00%。
- 值得注意的风险点：入场以来浮盈 +5.00%。
"""

    def test_old_bracket_format_parsed(self):
        facts = build_report.review_facts(self.OLD_BLOCK, make_verify())
        self.assertEqual(facts["n_close"], 1)
        self.assertEqual(facts["n_ma"], 1)
        self.assertEqual(facts["n_dist"], 1)
        self.assertEqual(facts["n_daily"], 1)
        self.assertEqual(facts["mismatches_count"], 0)

    def test_new_plain_format_parsed(self):
        """模型复述数据行的写法（无括号）也必须能核对。"""
        facts = build_report.review_facts(self.NEW_BLOCK, make_verify())
        self.assertEqual(facts["n_close"], 1)
        self.assertEqual(facts["n_ma"], 1)
        self.assertEqual(facts["n_dist"], 1)
        self.assertEqual(facts["n_daily"], 1)
        self.assertEqual(facts["mismatches_count"], 0)

    def test_wrong_number_is_caught(self):
        block = self.NEW_BLOCK.replace("5.00%", "50.00%")
        facts = build_report.review_facts(block, make_verify())
        self.assertGreater(facts["mismatches_count"], 0)

    def test_unparseable_review_flagged_not_passed(self):
        """完全无法解析的 review 不能静默记为核对通过。"""
        facts = build_report.review_facts("【600001】\n- 全是文字，没有任何数字。\n", make_verify())
        self.assertEqual(facts["n_close"] + facts["n_ma"] + facts["n_dist"] + facts["n_daily"], 0)


class EndToEndTest(_CfgMixin, unittest.TestCase):
    def test_example_data_builds_report(self):
        """examples 全套数据 → 报告生成成功且包含五个章节。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.cfg(tmp, output_name="report.md")
            report, report_path, expected_date = build_report.build_report(
                cfg, make_verify(),
                (EXAMPLES / "review_example.md").read_text(encoding="utf-8"),
                *build_report.load_buy_signals(EXAMPLES / "buy_signals_example.csv"),
                _NullLogger(),
            )
            self.assertEqual(expected_date, "2026-11-06")
            for section in ("今日概览", "持仓复盘", "明日待执行", "风险提示", "待核对"):
                self.assertIn(section, report)
            # pending 示例行进入待执行
            self.assertIn("600004", report)
            # 数字核对通过（无「不一致」字样出现在核对行）
            self.assertNotIn("dist_pct 重算不一致", report)


class _NullLogger:
    """测试用的空日志器。"""

    def info(self, message):
        pass

    def warn(self, message):
        pass

    def error(self, message):
        pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
