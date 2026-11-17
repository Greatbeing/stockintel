#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""可选的数据层参考实现：AkShare 前复权行情 → verify JSON 骨架。

主流程保持零第三方依赖；本脚本放在 contrib/，只在想自动拉行情时使用：
  pip install akshare
  python contrib/fetch_verify.py --codes 600001,600519 --out data/verify_20261117.json \
      [--date 20261117] [--portfolio portfolio.json]

行为：
- 用东财前复权日线计算 close / prev_close / MA233 / dist_pct（与主流程口径一致）
- 持仓字段（入场价、浮盈、止损等）行情里没有，来自 --portfolio JSON 合并
  （{code: {holding_now, exec_date, exec_price, unreal, stop_pending, prev_class, ...}}）；
  未提供的记 null / holding_now=false，绝不编造
- --date 给定时要求最新交易日与之相符（休市或数据未更新则停止）

退出码：0 成功；3 未安装 akshare；4 历史不足 / 日期不符。
"""
import argparse
import json
import sys
from pathlib import Path

MIN_HISTORY = 233  # MA233 需要至少 233 根日线，另需 1 根算 prev_close

SKELETON_FIELDS = {
    "holding_now": False,
    "prev_entry_date": None,
    "prev_entry_price": None,
    "exec_date": None,
    "exec_price": None,
    "unreal": None,
    "stop_pending": False,
    "status": "watch",
    "prev_class": "watch",
}


class FetchError(RuntimeError):
    """行情或参数不满足产出 verify 的条件，必须停止。"""


def compute_rows(history, names=None, portfolio=None):
    """纯计算：{code: [(date, close), ...] 升序} → verify 记录列表。

    不依赖 akshare / pandas，可独立测试。
    """
    names = names or {}
    portfolio = portfolio or {}
    rows = []
    for code in sorted(history):
        series = history[code]
        if len(series) < MIN_HISTORY + 1:
            raise FetchError(
                "%s 历史不足：%d 根 < %d 根（MA233 + prev_close）"
                % (code, len(series), MIN_HISTORY + 1)
            )
        closes = [float(c) for _d, c in series]
        data_date = str(series[-1][0])
        close = closes[-1]
        prev_close = closes[-2]
        ma233 = round(sum(closes[-MIN_HISTORY:]) / MIN_HISTORY, 4)
        if ma233 == 0:
            raise FetchError("%s MA233 为 0，无法计算 dist_pct" % code)
        dist_pct = round((close / ma233 - 1.0) * 100.0, 2)
        row = dict(SKELETON_FIELDS)
        row.update(
            code=code,
            name=names.get(code, code),
            data_date=data_date,
            close=close,
            prev_close=prev_close,
            ma233=ma233,
            dist_pct=dist_pct,
        )
        # 持仓字段只来自用户提供的 portfolio，没有就不造数
        row.update(portfolio.get(code, {}))
        rows.append(row)
    return rows


def check_date(rows, wanted):
    """wanted: YYYYMMDD 或 None。全部 data_date 必须一致且与 wanted 相符。"""
    dates = {r["data_date"] for r in rows}
    if len(dates) != 1:
        raise FetchError("各标的最新交易日不一致：%s" % ",".join(sorted(dates)))
    actual = next(iter(dates))
    if wanted:
        norm = "%s-%s-%s" % (wanted[0:4], wanted[4:6], wanted[6:8])
        if actual != norm:
            raise FetchError(
                "最新交易日 %s 与 --date %s 不符（休市或行情未更新）" % (actual, norm)
            )
    return actual


def fetch_history(codes, lookback_days=520):
    """拉取前复权日线。惰性 import：不装 akshare 时其余功能仍可测试。"""
    try:
        import akshare as ak
    except ImportError:
        raise FetchError(
            "akshare 未安装：pip install akshare（contrib 可选依赖，主流程无需）"
        )
    import datetime as dt

    end = dt.date.today()
    start = end - dt.timedelta(days=lookback_days)
    # 名称取自实时快照表（一次调用）；失败时退回用代码当名称
    names = {}
    try:
        spot = ak.stock_zh_a_spot_em()
        for _, r in spot.iterrows():
            names[str(r["代码"])] = str(r["名称"])
    except Exception:
        pass
    history = {}
    for code in codes:
        df = ak.stock_zh_a_hist(
            symbol=code, period="daily",
            start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"),
            adjust="qfq",
        )
        if df is None or len(df) == 0:
            raise FetchError("%s 未返回行情数据" % code)
        history[code] = [
            (str(d)[:10], float(c)) for d, c in zip(df["日期"], df["收盘"])
        ]
    return history, names


def main(argv=None):
    ap = argparse.ArgumentParser(description="fetch qfq history → verify JSON skeleton (optional, needs akshare)")
    ap.add_argument("--codes", required=True, help="逗号分隔的 6 位代码")
    ap.add_argument("--out", required=True, help="verify JSON 输出路径")
    ap.add_argument("--date", default=None, help="YYYYMMDD，要求最新交易日与之相符")
    ap.add_argument("--portfolio", default=None, help="持仓合并 JSON：{code: {字段...}}")
    args = ap.parse_args(argv)

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not codes:
        print("no codes given", file=sys.stderr)
        return 4
    portfolio = {}
    if args.portfolio:
        with open(args.portfolio, encoding="utf-8") as f:
            portfolio = json.load(f)

    try:
        history, names = fetch_history(codes)
        rows = compute_rows(history, names=names, portfolio=portfolio)
        actual = check_date(rows, args.date)
    except FetchError as exc:
        print("FETCH STOPPED: %s" % exc, file=sys.stderr)
        return 3 if "akshare 未安装" in str(exc) else 4

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print("wrote %s (%d rows, data_date=%s)" % (out, len(rows), actual))
    return 0


if __name__ == "__main__":
    sys.exit(main())
