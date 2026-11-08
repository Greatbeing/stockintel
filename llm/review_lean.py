#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
review_lean.py -- 精简版 A 股每日文字复盘 (A-share only, v1.1)。

v1.1 相对 v1 的改动：
- 入场价统一用 exec_price（实际成交价），没有才回退到 prev_entry_price（信号价）。
- "当日浮动"标签改为"持仓浮盈"：该值实际就是 verify.unreal（持仓浮盈），不是当日涨跌。
- 数据目录不再写死：默认取仓库根目录下的 data/，可用 STOCKINTEL_RESULTS_DIR 环境变量指定。

借鉴思路 (只借思路, 不跑原仓库):
- ZhuLinsen/daily_stock_analysis strategies/chan_theory.yaml:
  结构 framing (中枢/背驰/一买二买三买) -- v1 只借"趋势位置"的表述方式,
  不做缠论信号判定, 因为 v1 没有分型/笔/线段数据.
- 同仓库 templates/report_markdown.j2: 每只票固定仪表盘 --
  趋势 / 风险警报 / 催化 / 一句话结论.

数据: 只读当日 verify JSON (生产筛股的东财前复权数据, AkShare).
v1 不新建数据拉取.

硬规则 (写在 prompt 里, 也由脚本结构保证):
- 模型只写文字点评, 不给买/卖/持有/加仓/减仓建议, 不写目标价,
  不改动 Buy3 / sell1 规则.
- 不编造新闻、财报、公告、资金流、机构观点或任何未给出的数字.
- 数据不支持的判断写"数据不足", 不要猜.
- 输出 Markdown, 每只票固定四节.

LLM: opencode run --model magpie/nvidia/nvidia/nemotron-3-ultra-550b-a55b
(免费模型，NVIDIA NIM 经 Magpie（Nemotron 3 Ultra 550B）；备选 Magpie LongCat（magpie/longcat/LongCat-2.5）。如遇 free usage limit 属免费额度耗尽，等恢复或换 --model 指定其他免费模型，脚本绝不静默换模型).
全程不放任何 key / token / 密码.

用法:
  python3 llm/review_lean.py --date 20261106 --stocks 600001,600002,600003
  python3 llm/review_lean.py --date 20261106                # 全部持仓 (holding_now)
  python3 llm/review_lean.py --date 20261106 --dry-run       # 只打印 prompt, 不调模型
  python3 llm/review_lean.py --verify data/verify_20261106.json --date 20261106 --stocks 600001
"""
import argparse
import json
import os
import subprocess
import sys
import time

# 数据目录：默认取仓库根目录下的 data/，可用环境变量 STOCKINTEL_RESULTS_DIR 指定
# （例如部署在原有 quant 工作区时，设为该工作区的 results/daily 目录）。
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS_DIR = os.environ.get("STOCKINTEL_RESULTS_DIR", os.path.join(REPO_ROOT, "data"))
DEFAULT_MODEL = "magpie/nvidia/nvidia/nemotron-3-ultra-550b-a55b"
BATCH_SIZE = 3
MODEL_TIMEOUT = 240  # seconds per opencode call

PROMPT_HEAD = """你是 A 股持仓文字复盘助手，只做文字点评，不做交易决策。

硬规则（违反即不合格）：
1. 不给出买入、卖出、持有、加仓、减仓建议，不写目标价，不评价买卖时机，不改动任何既有交易规则。
2. 只使用下面给出的数据。不编造新闻、财报、公告、资金流、机构观点、行业数据或任何没有给出的数字。
3. 数据不支持的判断，写“数据不足”，不要猜测。
4. 不要声称识别出缠论买卖点（一买/二买/三买）、中枢或背驰——没有提供分型、笔、线段数据，无法判断。只描述价格相对 MA233 的位置。
5. “关注点/催化”只能写由给出的数据直接引出的问题（例如“能否站回 MA233”“止损线是否临近”），不要编造事件催化。
6. 每只股票不超过约 120 字。

数据（{date} 收盘，东财前复权）：
{data_lines}

输出格式（纯 Markdown，不要开场白、不要总结）：
按上面数据的顺序，每只股票一段；段首写【代码】，然后固定四行：
【600001】
- 趋势位置：
- 值得注意的风险点：
- 关注点/催化：
- 一句话结论：
"""


def pct(a, b):
    if a is None or b in (None, 0):
        return None
    return (a / b - 1.0) * 100.0


def fmt_pct(v, nd=2):
    if v is None:
        return "—"
    return "%+.*f%%" % (nd, v)


def stock_data_line(s):
    """One compact, precomputed data line per stock (no arithmetic left to the model)."""
    code = s.get("code", "?")
    name = s.get("name", "?")
    close = s.get("close")
    prev_close = s.get("prev_close")
    ma233 = s.get("ma233")
    dist = s.get("dist_pct")
    # v1.1: 入场价统一用实际成交价 exec_price（有则用，没有回退到 prev_entry_price）。
    # 旧版用 prev_entry_price（信号价）会在当日执行的票上虚增/虚减浮盈。
    if s.get("exec_price") is not None:
        entry_date = s.get("exec_date") or s.get("prev_entry_date")
        entry_price = s.get("exec_price")
    else:
        entry_date = s.get("prev_entry_date")
        entry_price = s.get("prev_entry_price")
    unreal = s.get("unreal")
    stop_pending = s.get("stop_pending")
    status = s.get("status", "?")
    day_chg = pct(close, prev_close)
    entry_float = pct(close, entry_price)
    above = "上方" if (dist is not None and dist >= 0) else "下方"
    parts = [
        "%s %s" % (code, name),
        "收盘 %.2f" % close if close is not None else "收盘 —",
        "昨收 %.2f" % prev_close if prev_close is not None else "昨收 —",
        "单日 %s" % fmt_pct(day_chg),
        "MA233 %.2f" % ma233 if ma233 is not None else "MA233 —",
        "收盘在 MA233 %s %s" % (above, fmt_pct(dist).replace("+", "") if dist is not None else "—"),
        "入场 %s @ %.2f" % (entry_date, entry_price) if entry_price else "入场 —",
        "入场以来浮盈 %s" % fmt_pct(entry_float),
        "持仓浮盈 %s" % fmt_pct(unreal),
        "止损待触发 %s" % ("是" if stop_pending else "否"),
        "状态 %s" % status,
    ]
    return "- " + "，".join(parts)


def build_prompt(stocks, date):
    data_lines = "\n".join(stock_data_line(s) for s in stocks)
    return PROMPT_HEAD.format(date=date, data_lines=data_lines)


def call_model(prompt, model):
    """One opencode call. Returns (stdout, elapsed_sec). Raises on failure."""
    t0 = time.time()
    cmd = ["opencode", "run", "--model", model, prompt]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=MODEL_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError("model call timed out after %ds" % MODEL_TIMEOUT)
    el = time.time() - t0
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0 or not out:
        raise RuntimeError(
            "model call failed rc=%s elapsed=%.1fs stderr=%.300s" % (r.returncode, el, err)
        )
    low = (out + err).lower()
    if "free usage limit" in low or "insufficient account funds" in low or "quota" in low:
        raise RuntimeError("model quota error: %s" % out[:300])
    return out, el


def load_verify(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("verify file is not a JSON list: %s" % path)
    return data


def main():
    ap = argparse.ArgumentParser(description="Lean A-share daily review (commentary only).")
    ap.add_argument("--verify", default=None, help="verify JSON path")
    ap.add_argument("--date", default=None, help="YYYYMMDD, default from verify data_date")
    ap.add_argument("--stocks", default=None, help="comma-separated codes, default all holding_now")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    ap.add_argument("--out", default=None, help="output md path")
    ap.add_argument("--dry-run", action="store_true", help="print prompt, do not call model")
    args = ap.parse_args()

    verify_path = args.verify
    if verify_path is None:
        if not args.date:
            ap.error("--date is required when --verify is not given")
        verify_path = os.path.join(RESULTS_DIR, "verify_%s.json" % args.date)
    stocks_all = load_verify(verify_path)
    date = args.date or (stocks_all[0].get("data_date", "") if stocks_all else "")
    date = str(date).replace("-", "")
    if len(date) != 8:
        ap.error("cannot determine date (need YYYYMMDD)")

    if args.stocks:
        wanted = [c.strip() for c in args.stocks.split(",") if c.strip()]
        by_code = {s.get("code"): s for s in stocks_all}
        missing = [c for c in wanted if c not in by_code]
        if missing:
            ap.error("codes not in verify file: %s" % ",".join(missing))
        stocks = [by_code[c] for c in wanted]
    else:
        stocks = [s for s in stocks_all if s.get("holding_now")]

    if not stocks:
        print("no stocks selected", file=sys.stderr)
        return 1

    out_path = args.out or os.path.join(RESULTS_DIR, "review_%s.md" % date)
    batches = [stocks[i:i + args.batch_size] for i in range(0, len(stocks), args.batch_size)]

    print("date=%s stocks=%d batches=%d model=%s" % (date, len(stocks), len(batches), args.model))
    sections = []
    total_elapsed = 0.0
    for bi, batch in enumerate(batches, 1):
        prompt = build_prompt(batch, date)
        if args.dry_run:
            print("===== batch %d prompt =====" % bi)
            print(prompt)
            continue
        print("batch %d/%d: %s ..." % (bi, len(batches), ",".join(s["code"] for s in batch)), flush=True)
        out, el = call_model(prompt, args.model)
        total_elapsed += el
        print("  -> %.1fs, %d chars" % (el, len(out)), flush=True)
        sections.append(out)

    if args.dry_run:
        return 0

    lines = [
        "# 每日文字复盘 %s" % date,
        "",
        "- 模型：%s" % args.model,
        "- 标的：%d 只（持仓跟踪）" % len(stocks),
        "- 数据：%s（东财前复权，AkShare）" % os.path.basename(verify_path),
        "- 文字复盘，非交易信号。点评不改变 Buy3 / sell1 规则，不构成买卖建议。",
        "",
    ]
    for sec in sections:
        lines.append(sec)
        lines.append("")
    lines.append("---")
    lines.append("生成用时：模型调用合计 %.1f 秒，共 %d 批。" % (total_elapsed, len(batches)))

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("wrote %s (%.1fs model time)" % (out_path, total_elapsed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
