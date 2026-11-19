# 贡献指南

欢迎改进 stockintel。先读懂它的定位：**完全免费、零第三方依赖、诚信优先的 A 股日报组装器**。贡献请强化这些特质，而不是稀释它们。

## 不可动摇的边界

这些是项目立场，不是待优化的缺点。请不要提交违背它们的改动：

- **LLM 只写文字点评，永远不做交易信号。** 不给买/卖/持有/加仓/减仓建议，不写目标价，不打分，不输出「买入/观望/卖出」决策。这是本仓库与多数「AI 荐股」项目的根本区别。
- **verify JSON 是唯一事实源。** 数字只能来自输入文件；组装器只做计数、百分比、比较。不拉新数据进主流程，不编造任何数字。
- **对不上就写「待核对」，不猜测、不绕过、不改写原文。**
- **免费额度耗尽即报错停止。** 不静默扣费，不静默换模型。
- **主流程零第三方依赖。** 只用 Python 3.10+ 标准库。可选依赖（如 akshare）只能放 `contrib/`，且惰性 import、缺失时明确报错。

如果你想要「LLM 给买卖建议 / 打分 / 实时行情 / 新闻推送」，那是另一类系统（例如致谢里的 daily_stock_analysis），不是本仓库的方向。

## 开发循环

```bash
# 跑全部测试（零依赖）
python -m unittest discover -s tests -v

# 跑示例（输出到 gitignored 的 examples/.build/，不覆盖静态样本）
python report/build_report.py --config examples/config.yaml
```

- 改了组装器的口径或结构：`tests/test_example_golden.py` 会失败。这是有意的回归守护——请同步更新 `examples/report_example.md`，并在 PR 里说明口径变化。
- 改了 review 自查正则：补一条能抓住该写法的测试（参考 `tests/test_build_report.py` 的 `ReviewFactsTest`）。
- 新增配置项：照 `config.example.yaml` 补注释，缺省值保持向后兼容。

## 提交

- 一个 PR 做一件事；提交信息说清「为什么」。
- 不提交真实行情、持仓、密钥。`data/`、`config.yaml`、`.env` 已在 `.gitignore`。
- 文档与代码同步：改了行为就改 `docs/ARCHITECTURE.md` / README，别让文档说谎。

## 报告问题

请附：运行命令、`logs/build.log` 相关行、输入数据的**脱敏**片段（可改用 examples 里的 synthetic 数据复现）。见 [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)。