#!/bin/bash
# stockintel 每日运行：等当日 verify → LLM 复盘 → 组装日报。
#
# 这是作者在免费云机（InstaBox）上的部署示例；路径可用环境变量覆盖：
#   QUANT_DIR      量化工作区根目录（默认 /data/workspace/quant）
#   RESULTS_DIR    每日结果目录（默认 $QUANT_DIR/results/daily）
#   STOCKINTEL_DIR stockintel 目录（默认 $QUANT_DIR/stockintel）
#   MODEL          复盘模型（默认 magpie/longcat/LongCat-2.5，Magpie 免费路由）
#
# 用法：bash deploy/run_daily.sh [YYYYMMDD]（默认当天，Asia/Shanghai）
# 退出码：0 成功；1 复盘或组装失败（不写报告）；2 当日 verify 缺失（休市或选股任务未产出）。
set -u

QUANT_DIR="${QUANT_DIR:-/data/workspace/quant}"
RESULTS_DIR="${RESULTS_DIR:-$QUANT_DIR/results/daily}"
STOCKINTEL_DIR="${STOCKINTEL_DIR:-$QUANT_DIR/stockintel}"
MODEL="${MODEL:-magpie/longcat/LongCat-2.5}"

cd "$STOCKINTEL_DIR" || exit 1

DATE="${1:-$(TZ=Asia/Shanghai date +%Y%m%d)}"
VERIFY="$RESULTS_DIR/verify_${DATE}.json"
REVIEW="$RESULTS_DIR/review_${DATE}.md"
LOG="logs/run_${DATE}.log"
mkdir -p logs
exec > >(tee -a "$LOG") 2>&1

echo "stockintel daily start date=$DATE model=$MODEL"
# 1. 等当日 verify（选股任务 16:39 跑，约 17:50 完成；最长等 30 分钟）
for i in $(seq 1 30); do
  if [ -f "$VERIFY" ]; then break; fi
  if [ "$i" -eq 30 ]; then
    echo "ERROR verify 缺失：$VERIFY（休市或选股任务未产出），跳过"
    echo "stockintel daily end: SKIP"
    exit 2
  fi
  sleep 60
done
echo "verify OK：$VERIFY"

# 2. 生成当日配置
cat > config.yaml << EOF
# stockintel 配置（run_daily.sh 每日生成，勿手改）
project: stockintel
version: M1
date: "$DATE"
quant_root: $QUANT_DIR

inputs:
  verify: results/daily/verify_${DATE}.json
  review: results/daily/review_${DATE}.md
  buy_signals: results/daily/buy_signals_${DATE}.csv

output:
  report: results/daily/report_${DATE}.md
  log: logs/build.log

model:
  name: $MODEL
  used_in_m1: false
EOF

# 3. LLM 复盘（免费模型；额度耗尽即报错，不静默换模型）
export STOCKINTEL_RESULTS_DIR="$RESULTS_DIR"
echo "review start model=$MODEL"
python3 "$QUANT_DIR/review_lean.py" --verify "$VERIFY" --date "$DATE" --model "$MODEL" --out "$REVIEW" || {
  echo "ERROR review 失败，不写报告"
  echo "stockintel daily end: FAILED"
  exit 1
}
echo "review OK：$REVIEW"

# 4. 组装日报
echo "build start"
python3 report/build_report.py --config config.yaml || {
  echo "ERROR build 失败，不写报告"
  echo "stockintel daily end: FAILED"
  exit 1
}
echo "stockintel daily end: OK report=$RESULTS_DIR/report_${DATE}.md"
