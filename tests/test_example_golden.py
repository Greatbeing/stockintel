#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""golden 回归：examples 组装结果必须与已提交样本 report_example.md 一致。

防止组装器改动悄悄改变日报口径/结构。运行时刻的「生成时间」归一后对比。
零第三方依赖。运行：python -m unittest discover -s tests -v
"""
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "report"))

import build_report  # noqa: E402

EXAMPLES = REPO_ROOT / "examples"


def _norm(text):
    return re.sub(r"生成时间：.*", "生成时间：<TS>", text)


class _NullLogger:
    def info(self, message):
        pass

    def warn(self, message):
        pass

    def error(self, message):
        pass


class ExampleGoldenTest(unittest.TestCase):
    def test_example_report_matches_committed_sample(self):
        cfg = build_report.load_config(EXAMPLES / "config.yaml")
        verify = build_report.load_verify(EXAMPLES / "verify_example.json", "2026-11-06")
        review = (EXAMPLES / "review_example.md").read_text(encoding="utf-8")
        rows, pending = build_report.load_buy_signals(EXAMPLES / "buy_signals_example.csv")
        report, _path, _date = build_report.build_report(
            cfg, verify, review, rows, pending, _NullLogger()
        )
        sample = (EXAMPLES / "report_example.md").read_text(encoding="utf-8")
        self.assertEqual(
            _norm(report), _norm(sample),
            "examples 组装结果与已提交样本不一致：若是有意的口径/结构改动，"
            "请同步更新 examples/report_example.md 并在 PR 说明。",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)