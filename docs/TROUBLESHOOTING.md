# 故障排查

按报错对号入座。所有「停止」都是有意设计：**宁可停下报错，不静默降级、不扣费、不猜数。**

## LLM 复盘相关（llm/review_lean.py）

### `model quota error` / `free usage limit` / `insufficient account funds`
- **原因**：免费模型额度耗尽。
- **处理**：等额度恢复；或 `--model` 指定另一个免费模型（如 `magpie/longcat/LongCat-2.5`）。脚本**不会**静默换模型。组装器 `build_report.py` 不调模型，不受影响。

### `model call timed out after 240s`
- **原因**：单次 opencode 调用超时（免费模型约 20 秒/次，偶发更慢）。
- **处理**：重试；或减小 `--batch-size`（默认 3）让每批更短。

### `model call failed rc=...`
- **原因**：opencode 未安装 / 未配置该模型渠道。
- **处理**：确认 `opencode` 可用且已配置免费模型；`--dry-run` 只打印 prompt 不调模型，可先验证输入。

## 数据相关

### `verify file not found` / 当日 verify 缺失（run_daily.sh 退出码 2）
- **原因**：休市，或你的选股任务还没产出当日 verify。
- **处理**：确认交易日与选股任务；数据由你的筛股流程产出，本仓库不拉行情。想自动拉行情可用 `contrib/fetch_verify.py`（需 `pip install akshare`）。

### `verify data_date mismatch: config=... file=...`
- **原因**：config 的 `date` 与 verify 里 `data_date` 不一致。
- **处理**：两者必须同为 `YYYYMMDD` 对应的同一交易日。全表 `data_date` 也必须一致。

### `verify file has no holding_now=true records`
- **原因**：verify 里没有当前持仓。
- **处理**：日报以持仓为对象；确认 `holding_now` 字段。非持仓记录只进构成统计。

### `akshare 未安装`（contrib/fetch_verify.py 退出码 3）
- **处理**：`pip install akshare`。这是 contrib 可选依赖，主流程不需要。

### `历史不足：N 根 < 234 根`（退出码 4）
- **原因**：MA233 需要至少 233 根日线 + 1 根算昨收。
- **处理**：次新股历史不够时无法算 MA233，属正常停止。

## 组装相关（report/build_report.py）

### `config missing key` / `config line N: expected 'key: value'`
- **原因**：config.yaml 缺字段或写法不支持（本项目用极简两级 YAML 解析，不支持 tab 缩进/复杂结构）。
- **处理**：照 `config.example.yaml` 补齐；用空格缩进。

### 报告「待核对」里出现 `dist_pct 重算不一致` / `review 行情数字与 verify 不一致`
- **原因**：输入数字自相矛盾，或 review 复述的数字与 verify 对不上。
- **处理**：这是核对在起作用。核对源数据；不要为了「让报告干净」去改核对容差。

### `未能从 review 文本解析出可核对数字`
- **原因**：review 写法与自查正则不匹配（自查覆盖有限，见 README 已知限制）。
- **处理**：按 `docs/ARCHITECTURE.md` 的固定四行格式写 review；或人工核对该段。本项未核对会如实列出，不会假装通过。

## 仍然解决不了

开 issue 附：运行命令、`logs/build.log` 相关行、脱敏输入片段（最好用 examples 复现）。