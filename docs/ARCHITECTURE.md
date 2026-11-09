# 架构

## 四层

1. **数据层**：AkShare + 东财前复权。verify JSON 是唯一事实源；本仓库不拉取行情。
2. **信号层**：Buy3 / sell1、MA233 趋势、golden 等确定性规则。信号由你自己的筛股流程产出，本仓库只消费其输出，不实现、不验证规则本身。
3. **LLM 点评层**（`llm/review_lean.py`）：DeepSeek 免费模型，只写文字点评。每只票固定四行：趋势位置 / 值得注意的风险点 / 关注点/催化 / 一句话结论。数字全部由脚本预先算好写进 prompt，模型不算数；硬规则禁止模型给买卖建议、目标价，禁止编造未给出的数字。
4. **报告层**（`report/build_report.py`）：纯组装，不调用模型。读取 verify JSON + review Markdown + buy_signals CSV，生成一份日报，并逐只复核数字。

## 数据流

```
筛股流程（你的）→ verify JSON + buy_signals CSV
→ llm/review_lean.py → review Markdown
→ report/build_report.py → report Markdown
```

## 输入数据格式

### verify JSON

JSON 列表，每只一条。必填字段：

| 字段 | 含义 |
|---|---|
| code | 6 位股票代码 |
| name | 股票名称 |
| data_date | 数据日期，`YYYY-MM-DD`，全表必须一致 |
| close | 收盘价 |
| prev_close | 前一日收盘价 |
| ma233 | 233 日均线 |
| dist_pct | `(close/ma233-1)*100`，保留两位小数 |
| holding_now | 是否当前持仓 |
| prev_entry_date / prev_entry_price | 信号入场日期 / 价格 |
| exec_date / exec_price | 实际成交日期 / 价格（当日执行的 pending 有值） |
| unreal | 持仓浮盈（相对成交价），不是当日涨跌 |
| stop_pending | 止损是否待触发 |
| status | 状态，如 `ok` |
| prev_class | 上一分类，如 `holding_recent` / `holding_old` / `pending` |

入场价口径（v1.1）：`exec_price`（实际成交价）优先，没有才回退到 `prev_entry_price`（信号价）。旧版用信号价会在当日执行的票上虚增/虚减浮盈，v1.1 已修正。

### review Markdown

每只持仓一段，段首写 ``，然后固定四行：趋势位置 / 值得注意的风险点 / 关注点/催化 / 一句话结论。由 `llm/review_lean.py` 生成，或按此格式手写。

### buy_signals CSV

列：`code, name, data_date, buy3_date, close, ma233, dist_pct, vol_ratio, recent_buy3, golden_date, class`。`class == "pending"` 的行进入「明日待执行」。文件不存在时该节写「数据不足」。

## 日报结构

1. 今日概览：持仓数、MA233 上方/下方计数、涨跌分布、最大涨跌
2. 持仓复盘：review 全文引用，未删改
3. 明日待执行：pending 清单（信号状态，不代表已成交）
4. 风险提示：止损待触发、距 MA233 ±2% 内、单日跌幅 ≥6%、浮亏 ≤-20%
5. 待核对：自动核对通过项 + 口径差异与未覆盖项（不做推断，仅列出）

## 自查规则

- 计数自洽（上方 + 下方 + 持平 = 持仓数）
- `dist_pct` 可由 `(close/ma233-1)*100` 复现（容差 0.02 个百分点）
- review 覆盖：每只持仓有且仅有一个 `` 段落
- review 里的收盘、MA233、距 MA233、单日涨跌与 verify 对得上（容差内）
- 口径差异（如入场价信号价 vs 成交价）写入「待核对」，不改写原文

## 信号规则说明

- Buy3 / sell1、8% 止损等规则来自作者自有系统，不在本仓库实现
- 233 趋势 overlay：ETF 回测有效（6 只中 5 只改进），个股默认关闭；个股上 regime 曾让 6/9 变差，未重新验证前不启用

## 维护说明

本项目由 dsh-superagent 协助维护。
