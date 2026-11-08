# stockintel — 完全免费的 A 股智能分析系统

每天一份 A 股日报：确定性信号 + LLM 文字点评，全程零付费。

> 非投资建议。本系统只做复盘与提醒，不构成买卖建议；LLM 文字永远不做交易信号。

## 免费清单

| 环节 | 用什么 | 花多少钱 |
|---|---|---|
| 行情数据 | AkShare（开源免费）+ 东财前复权 | 0 |
| LLM 点评 | DeepSeek 免费模型（经 OpenCode / Magpie 免费渠道） | 0 |
| 运行环境 | 本地电脑，或免费云机 | 0 |
| 代码依赖 | Python 3.10+ 标准库，零第三方包 | 0 |
| 订阅 / API Key | 不需要付费订阅；免费渠道自带额度 | 0 |

免费额度耗尽时脚本报错停止，绝不静默扣费、绝不静默换模型。详见 [docs/FREE.md](docs/FREE.md)。

## 它做什么

- 每天收盘后，把你的持仓核实表（verify JSON）变成一份日报：今日概览、持仓复盘、明日待执行、风险提示、待核对
- 持仓复盘由 LLM 写文字点评（趋势位置、风险点、关注点、一句话结论），数字全部预先算好，模型只写字，不算数
- 组装器逐只复核数字：计数、MA233 位置、涨跌幅、浮盈口径，对不上的写进「待核对」，不猜测

## 它不做什么

- 不做实盘，不下单，不碰券商
- LLM 不给买/卖/持有建议，不写目标价，不改任何交易规则
- 不提供实时行情，不拉取新数据（数据由你自己的筛股流程产出，本仓库不含真实行情）

## 架构

```
数据层（AkShare / 东财前复权，verify JSON 为唯一事实源）
  → 信号层（Buy3 / sell1、MA233、golden 等确定性规则）
  → LLM 点评层（llm/review_lean.py，DeepSeek 免费模型，只写文字）
  → 报告层（report/build_report.py，纯组装，不调模型）
```

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 快速开始

```bash
git clone https://github.com/Greatbeing/stockintel.git
cd stockintel

# 1. 准备数据：把你的 verify JSON / review Markdown / buy_signals CSV 放到 data/
#    字段格式见 docs/ARCHITECTURE.md；没有数据可先跑示例（第 4 步）

# 2. 配好 config.yaml（照着 config.example.yaml 复制）
cp config.example.yaml config.yaml

# 3. （可选）生成持仓文字复盘，需要装好 opencode + 免费模型
python3 llm/review_lean.py --verify data/verify_20261106.json --date 20261106

# 4. 生成日报
python3 report/build_report.py

# 5. 看报告
#    data/report_20261106.md
```

没有数据？先跑示例，一条命令出日报：

```bash
python3 report/build_report.py --config examples/config.yaml
# 输出：examples/report_example.md
```

## 示例输出

见 [examples/report_example.md](examples/report_example.md)：3 只示例持仓的日报，含概览、复盘、待执行、风险提示、待核对五部分。

## 已知限制

- LLM 点评的质量取决于免费模型额度与输入数据；额度耗尽时复盘步骤会报错停止
- 233 趋势规则在 ETF 上回测有效，个股默认关闭（详见 docs/ARCHITECTURE.md）
- 自查正则覆盖有限：个别写法的数字可能漏检，见报告「待核对」
- 本系统不验证你的信号规则本身；规则有效性需要你自己回测

## 致谢

- 复盘结构思路借鉴 [ZhuLinsen/daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)
- 行情数据来自 [AkShare](https://github.com/akfamily/akshare)

## License

MIT
