#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stockintel M1 日报组装器。

定位：纯组装，不调用模型，不计算新指标。
输入：config.yaml 指定的 verify JSON、review Markdown、buy_signals CSV。
输出：config.yaml 指定的报告路径；日志写到 stockintel/logs/build.log。

硬规则：
- 所有数字取自输入文件；仅做计数、百分比和简单比较。
- 交叉核对不通过的地方写入报告末尾「待核对」，不猜测、不绕过。
- 输入缺失或结构错误立即停止，并把错误写进 build.log。
"""

import argparse
import csv
import json
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = BASE_DIR / "config.yaml"
REVIEW_BLOCK_RE = re.compile(r"^【(\d{6})】", re.MULTILINE)
DIST_TOLERANCE = 0.02  # 个百分点；dist_pct 在源文件中保留两位小数


class BuildError(RuntimeError):
    """输入或配置不满足组装条件，必须停止。"""


class BuildLogger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a", encoding="utf-8")
        self.info("=" * 72)
        self.info("stockintel M1 build start")

    def _write(self, level, message):
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = "[%s] %-5s %s" % (stamp, level, message)
        print(line)
        self.fh.write(line + "\n")
        self.fh.flush()

    def info(self, message):
        self._write("INFO", message)

    def warn(self, message):
        self._write("WARN", message)

    def error(self, message):
        self._write("ERROR", message)

    def close(self, status):
        self.info("stockintel M1 build end: %s" % status)
        self.fh.close()


def parse_scalar(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value


def load_config(path):
    """读取本项目的简单两级 YAML（不依赖 PyYAML）。

    支持：注释、空行、`key: value`、缩进子映射、单/双引号、true/false。
    遇到不支持的写法直接报错，避免静默误读配置。
    """
    path = Path(path)
    if not path.is_file():
        raise BuildError("config not found: %s" % path)
    root = {}
    stack = [(-1, root)]
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            if "\t" in raw[: len(raw) - len(raw.lstrip())]:
                raise BuildError("config line %d: tab indentation is not supported" % lineno)
            indent = len(raw) - len(raw.lstrip(" "))
            text = raw.strip()
            key, sep, value = text.partition(":")
            if not sep or not key.strip():
                raise BuildError("config line %d: expected 'key: value'" % lineno)
            key = key.strip()
            while stack and indent <= stack[-1][0]:
                stack.pop()
            parent = stack[-1][1]
            if value.strip() == "":
                child = {}
                parent[key] = child
                stack.append((indent, child))
            else:
                parent[key] = parse_scalar(value)
    return root


def require(mapping, dotted, kind=dict):
    node = mapping
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise BuildError("config missing key: %s" % dotted)
        node = node[part]
    if kind is not None and not isinstance(node, kind):
        raise BuildError("config key %s has wrong type: %s" % (dotted, type(node).__name__))
    return node


def resolve_under(root, value):
    p = Path(str(value)).expanduser()
    return p if p.is_absolute() else Path(root) / p


def as_float(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise BuildError("expected number, got boolean: %r" % value)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text in ("", "—", "None", "null"):
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise BuildError("expected number, got %r" % value) from exc


def fmt_num(value, digits=2):
    if value is None:
        return "—"
    return ("%%.%df" % digits) % value


def fmt_pct(value, digits=2):
    if value is None:
        return "—"
    return ("%%+.%df%%%%" % digits) % value


def daily_pct(stock):
    close = as_float(stock.get("close"))
    prev_close = as_float(stock.get("prev_close"))
    if close is None or prev_close is None or prev_close == 0:
        raise BuildError(
            "cannot compute daily change for %s: close=%r prev_close=%r"
            % (stock.get("code"), stock.get("close"), stock.get("prev_close"))
        )
    return (close / prev_close - 1.0) * 100.0


def load_verify(path, expected_date):
    if not Path(path).is_file():
        raise BuildError("verify file not found: %s" % path)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list) or not data:
        raise BuildError("verify file must be a non-empty JSON list: %s" % path)
    dates = set()
    for item in data:
        if not isinstance(item, dict):
            raise BuildError("verify item is not an object: %r" % item)
        for key in ("code", "name", "data_date", "close", "ma233", "dist_pct", "holding_now"):
            if key not in item:
                raise BuildError("verify item %s missing key: %s" % (item.get("code", "?"), key))
        dates.add(str(item["data_date"]))
    if dates != {expected_date}:
        raise BuildError(
            "verify data_date mismatch: config=%s file=%s"
            % (expected_date, ",".join(sorted(dates)))
        )
    return data


def load_review(path):
    if not Path(path).is_file():
        raise BuildError("review file not found: %s" % path)
    text = Path(path).read_text(encoding="utf-8")
    if not text.strip():
        raise BuildError("review file is empty: %s" % path)
    return text


def load_buy_signals(path):
    """返回 (rows, pending_rows)。文件不存在时返回 (None, None)。"""
    path = Path(path)
    if not path.is_file():
        return None, None
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise BuildError("buy_signals has no header: %s" % path)
        rows = list(reader)
    pending = [r for r in rows if (r.get("class") or "").strip() == "pending"]
    return rows, pending


def review_facts(review_text, holdings):
    """提取 review 中可交叉核对的数字，仅用于「待核对」，不改写原文。"""
    by_code = {str(x["code"]): x for x in holdings}
    matches = list(REVIEW_BLOCK_RE.finditer(review_text))
    codes = [m.group(1) for m in matches]
    facts = {
        "codes": codes,
        "duplicates": sorted({c for c in codes if codes.count(c) > 1}),
        "daily_label_mismatches": [],
        "entry_basis_diffs": [],
        "close_mismatches": [],
        "ma_mismatches": [],
        "dist_mismatches": [],
        "daily_mismatches": [],
    }
    # 每个【代码】到下一个【代码】之间为一个正文块
    parsed = {}
    for idx, match in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(review_text)
        parsed.setdefault(match.group(1), review_text[match.end():end])
    for code, block in parsed.items():
        stock = by_code.get(code)
        if stock is None:
            continue
        close = as_float(stock.get("close"))
        ma233 = as_float(stock.get("ma233"))
        dist = as_float(stock.get("dist_pct"))
        unreal = as_float(stock.get("unreal"))
        day = daily_pct(stock)

        match = re.search(r"收盘\s*([0-9]+(?:\.[0-9]+)?)", block)
        if match and close is not None and abs(float(match.group(1)) - close) > 0.005:
            facts["close_mismatches"].append((code, match.group(1), close))

        match = re.search(r"MA233（([0-9]+(?:\.[0-9]+)?)）", block)
        if match and ma233 is not None and abs(float(match.group(1)) - ma233) > 0.02:
            facts["ma_mismatches"].append((code, match.group(1), round(ma233, 2)))

        match = re.search(r"MA233（[0-9.]+）\s*(上方|下方)\s*(-?[0-9]+(?:\.[0-9]+)?)%", block)
        if match and dist is not None:
            signed = abs(float(match.group(2))) if match.group(1) == "上方" else -abs(float(match.group(2)))
            if abs(signed - dist) > 0.06:
                facts["dist_mismatches"].append((code, match.group(0), dist))

        for match in re.finditer(r"单日\s*([+-]?[0-9]+(?:\.[0-9]+)?)%", block):
            if abs(float(match.group(1)) - day) > 0.06:
                facts["daily_mismatches"].append((code, match.group(1), round(day, 2)))

        claims = []
        for match in re.finditer(r"入场以来\s*[^0-9+\-]{0,6}([+-]?[0-9]+(?:\.[0-9]+)?)%", block):
            claims.append((float(match.group(1)), match.group(0)))
        for match in re.finditer(r"(?:浮盈|浮亏)[^0-9+\-]{0,12}([+-]?[0-9]+(?:\.[0-9]+)?)%", block):
            claims.append((float(match.group(1)), match.group(0)))
        if claims and unreal is not None:
            value, claim = max(claims, key=lambda item: abs(item[0] - unreal))
            if abs(value - unreal) > 0.06:
                facts["entry_basis_diffs"].append(
                    (code, str(stock.get("name")), value, unreal,
                     as_float(stock.get("prev_entry_price")), as_float(stock.get("exec_price")),
                     claim)
                )

        match = re.search(r"当日浮动\s*([+-]?[0-9]+(?:\.[0-9]+)?)%", block)
        if match and abs(float(match.group(1)) - day) > 0.06:
            facts["daily_label_mismatches"].append(
                (code, str(stock.get("name")), float(match.group(1)), round(day, 2), unreal)
            )
    return facts


def risk_table(items, include_stop=True):
    if not items:
        return "- 名单：无（0 只）\n"
    lines = [
        "| 代码 | 名称 | 收盘 | 距 MA233 | 单日涨跌 | 浮盈/亏 | 止损待触发 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for stock, day in items:
        lines.append(
            "| %s | %s | %s | %s | %s | %s | %s |"
            % (
                stock.get("code"),
                stock.get("name"),
                fmt_num(as_float(stock.get("close"))),
                fmt_pct(as_float(stock.get("dist_pct"))),
                fmt_pct(day),
                fmt_pct(as_float(stock.get("unreal"))),
                "是" if stock.get("stop_pending") is True else "否",
            )
        )
    return "\n".join(lines) + "\n"


def build_report(cfg, verify, review_text, signals_rows, pending_rows, logger):
    quant_root = Path(str(require(cfg, "quant_root", str)))
    date = str(require(cfg, "date", str))
    model_name = str(require(cfg, "model.name", str))
    verify_path = resolve_under(quant_root, require(cfg, "inputs.verify", str))
    review_path = resolve_under(quant_root, require(cfg, "inputs.review", str))
    signals_path = resolve_under(quant_root, require(cfg, "inputs.buy_signals", str))
    report_path = resolve_under(quant_root, require(cfg, "output.report", str))
    expected_date = "%s-%s-%s" % (date[0:4], date[4:6], date[6:8])

    holdings = [x for x in verify if x.get("holding_now") is True]
    non_holdings = [x for x in verify if x.get("holding_now") is not True]
    if not holdings:
        raise BuildError("verify file has no holding_now=true records")

    # ---------- 逐只数字与计数 ----------
    daily = {}
    for stock in holdings:
        daily[str(stock["code"])] = daily_pct(stock)
    above = [x for x in holdings if as_float(x["close"]) > as_float(x["ma233"])]
    below = [x for x in holdings if as_float(x["close"]) < as_float(x["ma233"])]
    flat = [x for x in holdings if as_float(x["close"]) == as_float(x["ma233"])]
    up = [x for x in holdings if daily[str(x["code"])] > 0]
    down = [x for x in holdings if daily[str(x["code"])] < 0]
    day_flat = [x for x in holdings if daily[str(x["code"])] == 0]
    max_up_stock = max(holdings, key=lambda x: daily[str(x["code"])])
    max_down_stock = min(holdings, key=lambda x: daily[str(x["code"])])

    dist_diffs = []
    max_dist_diff = 0.0
    for stock in holdings:
        close = as_float(stock["close"])
        ma233 = as_float(stock["ma233"])
        dist = as_float(stock["dist_pct"])
        if ma233 in (None, 0) or dist is None:
            raise BuildError("cannot recompute dist_pct for %s" % stock.get("code"))
        calc = (close / ma233 - 1.0) * 100.0
        diff = abs(calc - dist)
        max_dist_diff = max(max_dist_diff, diff)
        if diff > DIST_TOLERANCE:
            dist_diffs.append((str(stock["code"]), dist, round(calc, 2)))

    checks = []
    issues = []

    checks.append(
        "持仓计数：verify JSON 共 %d 条，holding_now=true 为 %d 只。"
        % (len(verify), len(holdings))
    )
    checks.append(
        "MA233 位置：逐只比较 close 与 ma233，上方 %d + 下方 %d + 持平 %d = %d，与概览一致。"
        % (len(above), len(below), len(flat), len(holdings))
    )
    if dist_diffs:
        issues.append(
            "dist_pct 重算不一致：%s（源值/重算值）。"
            % "、".join("%s %s/%s" % item for item in dist_diffs)
        )
    else:
        checks.append(
            "dist_pct：%d 只全部可由 (close/ma233-1)*100 复现，最大偏差 %.4f 个百分点（容差 %.2f），一致。"
            % (len(holdings), max_dist_diff, DIST_TOLERANCE)
        )

    # ---------- review 交叉核对 ----------
    facts = review_facts(review_text, holdings)
    holding_codes = {str(x["code"]) for x in holdings}
    review_codes = set(facts["codes"])
    missing_review = sorted(holding_codes - review_codes)
    extra_review = sorted(review_codes - holding_codes)
    if missing_review or extra_review or facts["duplicates"]:
        issues.append(
            "review 覆盖不一致：持仓缺 %s；review 多出 %s；重复 %s。"
            % (missing_review or "无", extra_review or "无", facts["duplicates"] or "无")
        )
    else:
        checks.append(
            "review 覆盖：%d 只持仓在 review 中均有且仅有一个【代码】段落，无缺漏、无重复。" % len(holdings)
        )
    if facts["close_mismatches"] or facts["ma_mismatches"] or facts["dist_mismatches"] or facts["daily_mismatches"]:
        issues.append(
            "review 行情数字与 verify 不一致：收盘 %s；MA233 %s；距 MA233 %s；单日 %s。"
            % (
                facts["close_mismatches"] or "无",
                facts["ma_mismatches"] or "无",
                facts["dist_mismatches"] or "无",
                facts["daily_mismatches"] or "无",
            )
        )
    else:
        checks.append("review 行情数字：收盘、MA233、距 MA233、单日涨跌均可与 verify 对上（容差内）。")

    # ---------- buy_signals pending ----------
    if signals_rows is None:
        issues.append("buy_signals 文件不存在；第三节写「数据不足，未生成」。")
        checks_pending = "buy_signals：文件不存在，未做 pending 核对。"
    else:
        bad_signal_date = []
        for row in pending_rows:
            if expected_date not in (row.get("recent_buy3") or ""):
                bad_signal_date.append(row.get("code"))
        if bad_signal_date:
            issues.append(
                "pending 行的 recent_buy3 未包含 %s：%s。"
                % (expected_date, "、".join(str(c) for c in bad_signal_date))
            )
        duplicates = sorted({r.get("code") for r in pending_rows if
                             sum(1 for q in pending_rows if q.get("code") == r.get("code")) > 1})
        if duplicates:
            issues.append("buy_signals pending 代码重复：%s。" % "、".join(duplicates))
        checks_pending = (
            "buy_signals：共 %d 行，class=pending 为 %d 只；%s。"
            % (
                len(signals_rows),
                len(pending_rows),
                "recent_buy3 均包含 %s" % expected_date
                if not bad_signal_date else "部分行日期不一致（见上）",
            )
        )
    checks.append(checks_pending)

    # ---------- 风险名单（全部来自 verify 数字） ----------
    stop_items = [(x, daily[str(x["code"])]) for x in holdings if x.get("stop_pending") is True]
    near_items = [(x, daily[str(x["code"])]) for x in holdings
                  if abs(as_float(x["dist_pct"])) <= 2.0]
    drop_items = [(x, daily[str(x["code"])]) for x in holdings
                  if daily[str(x["code"])] <= -6.0]
    loss_items = [(x, daily[str(x["code"])]) for x in holdings
                  if as_float(x.get("unreal")) is not None
                  and as_float(x.get("unreal")) <= -20.0]

    # 口径差异：review 的「入场以来」按 prev_entry_price 计算；verify.unreal 对当日执行的 pending 按 exec_price。
    entry_basis_codes = [c for c, _n, _v, _u, _pe, _ex, _claim in facts["entry_basis_diffs"]]
    if facts["entry_basis_diffs"]:
        examples = []
        for code, name, value, unreal, prev_entry, exec_price, _claim in sorted(
            facts["entry_basis_diffs"], key=lambda t: abs(t[2] - t[3]), reverse=True
        )[:3]:
            examples.append(
                "%s %s：review %s，verify.unreal %s（prev_entry_price=%s，exec_price=%s）"
                % (code, name, fmt_pct(value), fmt_pct(unreal),
                   fmt_num(prev_entry), fmt_num(exec_price))
            )
        review_basis_loss = [
            (code, name, value)
            for code, name, value, _u, _pe, _ex, _claim in facts["entry_basis_diffs"]
            if value <= -20.0
        ]
        note = (
            "review「入场以来浮盈」用 prev_entry_price 计算，verify.unreal 对当日执行的 pending 用 exec_price；"
            "%d 只口径不同（%s）。原文保留，未改写。示例：%s。"
            % (len(facts["entry_basis_diffs"]), "、".join(entry_basis_codes), "；".join(examples))
        )
        if review_basis_loss:
            note += (
                " 若按 review 口径，%s 会进入「浮亏≤-20%%」清单；"
                "本报告使用 verify.unreal，未列入。"
                % "、".join("%s %s %s" % (c, n, fmt_pct(v)) for c, n, v in review_basis_loss)
            )
        issues.append(note)

    if facts["daily_label_mismatches"]:
        sample = facts["daily_label_mismatches"][0]
        issues.append(
            "review 的「当日浮动」实际等于 verify.unreal（按执行价/入场价的浮盈），不是当日涨跌；"
            "%d 处该标签与单日涨跌不一致。例：%s %s 标注 %s，单日实为 %s。"
            % (
                len(facts["daily_label_mismatches"]),
                sample[0],
                sample[1],
                fmt_pct(sample[2]),
                fmt_pct(sample[3]),
            )
        )

    if non_holdings:
        class_counts = {}
        for stock in non_holdings:
            key = str(stock.get("prev_class", "unknown"))
            class_counts[key] = class_counts.get(key, 0) + 1
        checks.append(
            "verify 记录构成：%d 条 = 持仓 %d + 非持仓 %d（%s）；非持仓不进入持仓统计。"
            % (
                len(verify),
                len(holdings),
                len(non_holdings),
                "，".join("%s %d" % (k, v) for k, v in sorted(class_counts.items())),
            )
        )

    if pending_rows:
        verify_codes = {str(x["code"]) for x in verify}
        not_covered = [str(r.get("code")) for r in pending_rows if str(r.get("code")) not in verify_codes]
        if not_covered:
            issues.append(
                "buy_signals 的 %d 只 pending 不在 verify JSON 覆盖范围内：%s；"
                "其行情数字只来自 buy_signals CSV。"
                % (len(not_covered), "、".join(not_covered))
            )
        else:
            checks.append("buy_signals pending 全部在 verify JSON 中有对应记录。")

    # ---------- 组装 Markdown ----------
    lines = []
    lines.append("# 智能股票分析日报 %s" % date)
    lines.append("")
    lines.append("- 生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("- 数据日期：%s 收盘" % expected_date)
    lines.append("- 数据来源：`%s`；`%s`；`%s`"
                 % (verify_path.name, review_path.name, signals_path.name))
    lines.append("- 本报告为纯组装：不调用模型、不算新指标、不构成投资建议。")
    lines.append("")

    lines.append("## 一、今日概览")
    lines.append("")
    lines.append("- 持仓数量：**%d 只**（verify JSON 共 %d 条，非持仓 %d 条）。"
                 % (len(holdings), len(verify), len(non_holdings)))
    lines.append("- MA233 位置：上方 **%d 只** / 下方 **%d 只** / 持平 **%d 只**（逐只按收盘价与 MA233 比较，合计 %d）。"
                 % (len(above), len(below), len(flat), len(holdings)))
    lines.append("- 单日涨跌：上涨 %d 只 / 下跌 %d 只 / 平盘 %d 只；最大涨幅 **%s**（%s %s），最大跌幅 **%s**（%s %s）。"
                 % (
                     len(up), len(down), len(day_flat),
                     fmt_pct(daily[str(max_up_stock["code"])]),
                     max_up_stock["code"], max_up_stock["name"],
                     fmt_pct(daily[str(max_down_stock["code"])]),
                     max_down_stock["code"], max_down_stock["name"],
                 ))
    lines.append("- 数据来源与日期：")
    lines.append("  - `%s`：东财前复权，AkShare；%s 收盘，%d 条核实记录。" % (verify_path.name, expected_date, len(verify)))
    lines.append("  - `%s`：LLM 文字复盘，模型 `%s`；%d 段持仓点评。" % (review_path.name, model_name, len(facts["codes"])))
    lines.append("  - `%s`：Buy3 信号；%s，共 %s 行。"
                 % (signals_path.name, expected_date, len(signals_rows) if signals_rows is not None else "缺失"))
    lines.append("")

    lines.append("## 二、持仓复盘")
    lines.append("")
    lines.append("> 以下为 `%s` 全文引用，未删改。文字复盘只做点评，不是交易信号。" % review_path.name)
    lines.append("")
    lines.append(review_text.rstrip())
    lines.append("")

    lines.append("## 三、明日待执行")
    lines.append("")
    if signals_rows is None:
        lines.append("数据不足，未生成。")
        lines.append("")
    else:
        lines.append("筛选口径：`%s` 中 `class == \"pending\"`，即 Buy3 在 %s 收盘确认、尚未持仓、下一交易日开盘执行。"
                     % (signals_path.name, expected_date))
        lines.append("")
        if not pending_rows:
            lines.append("今日无 class=pending 的 Buy3 信号（数据源共 %d 行）。" % len(signals_rows))
            lines.append("")
        else:
            lines.append("命中 **%d 只**；`recent_buy3` 均包含 %s。" % (len(pending_rows), expected_date))
            lines.append("")
            lines.append("| 代码 | 名称 | 收盘 | MA233 | 距 MA233 | 量比 | 信号日 | verify 覆盖 |")
            lines.append("|---|---|---:|---:|---:|---:|---|---|")
            verify_codes = {str(x["code"]) for x in verify}
            for row in pending_rows:
                code = str(row.get("code"))
                lines.append(
                    "| %s | %s | %s | %s | %s | %s | %s | %s |"
                    % (
                        code,
                        row.get("name"),
                        fmt_num(as_float(row.get("close"))),
                        fmt_num(as_float(row.get("ma233"))),
                        fmt_pct(as_float(row.get("dist_pct"))),
                        fmt_num(as_float(row.get("vol_ratio"))),
                        row.get("buy3_date"),
                        "是" if code in verify_codes else "否",
                    )
                )
            lines.append("")
            lines.append("说明：pending 是信号状态，不代表已成交；明日执行价以开盘为准，本报告不预测。")
            lines.append("")

    lines.append("## 四、风险提示")
    lines.append("")
    lines.append("> 以下名单全部由 verify JSON 的数字直接筛选，不引入外部数据，不做推断。")
    lines.append("")
    lines.append("### 1. 止损待触发（stop_pending = true）")
    lines.append("")
    lines.append("- 计数：%d 只" % len(stop_items))
    lines.append(risk_table(stop_items))
    lines.append("### 2. 距 MA233 正负 2% 以内（|dist_pct| ≤ 2）")
    lines.append("")
    lines.append("- 计数：%d 只" % len(near_items))
    lines.append(risk_table(near_items))
    lines.append("### 3. 单日跌幅 ≥ 6%（由 close/prev_close 重算）")
    lines.append("")
    lines.append("- 计数：%d 只" % len(drop_items))
    lines.append(risk_table(drop_items))
    lines.append("### 4. 浮亏 ≤ -20%（verify.unreal 口径）")
    lines.append("")
    lines.append("- 计数：%d 只" % len(loss_items))
    lines.append(risk_table(loss_items))
    if facts["entry_basis_diffs"]:
        lines.append("> 注：若按 review 的 prev_entry_price 口径，浮亏名单会不同；口径差异见末尾「待核对」。")
        lines.append("")

    lines.append("## 待核对")
    lines.append("")
    lines.append("### 自动核对通过项")
    lines.append("")
    for item in checks:
        lines.append("- %s" % item)
    lines.append("")
    lines.append("### 口径差异与未覆盖项（不做推断，仅列出）")
    lines.append("")
    if issues:
        for idx, item in enumerate(issues, 1):
            lines.append("%d. %s" % (idx, item))
    else:
        lines.append("无。")
    lines.append("")

    return "\n".join(lines), report_path, expected_date


def main(argv=None):
    parser = argparse.ArgumentParser(description="stockintel M1 daily report builder (assembly only)")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="config.yaml path")
    args = parser.parse_args(argv)

    logger = BuildLogger(BASE_DIR / "logs" / "build.log")
    status = "FAILED"
    try:
        logger.info("step 1/7 读取配置: %s" % args.config)
        cfg = load_config(args.config)
        date = str(require(cfg, "date", str))
        if len(date) != 8 or not date.isdigit():
            raise BuildError("config date must be YYYYMMDD, got %r" % date)
        expected_date = "%s-%s-%s" % (date[0:4], date[4:6], date[6:8])
        quant_root = Path(str(require(cfg, "quant_root", str)))
        verify_path = resolve_under(quant_root, require(cfg, "inputs.verify", str))
        review_path = resolve_under(quant_root, require(cfg, "inputs.review", str))
        signals_path = resolve_under(quant_root, require(cfg, "inputs.buy_signals", str))
        report_path = resolve_under(quant_root, require(cfg, "output.report", str))
        logger.info("  date=%s quant_root=%s" % (date, quant_root))
        logger.info("  verify=%s" % verify_path)
        logger.info("  review=%s" % review_path)
        logger.info("  buy_signals=%s" % signals_path)
        logger.info("  report=%s" % report_path)

        logger.info("step 2/7 读取并校验 verify JSON")
        verify = load_verify(verify_path, expected_date)
        n_hold = sum(1 for x in verify if x.get("holding_now") is True)
        logger.info("  records=%d holdings=%d non_holdings=%d" % (len(verify), n_hold, len(verify) - n_hold))

        logger.info("step 3/7 读取 review Markdown")
        review_text = load_review(review_path)
        logger.info("  chars=%d blocks=%d" % (len(review_text), len(REVIEW_BLOCK_RE.findall(review_text))))

        logger.info("step 4/7 读取 buy_signals CSV")
        signal_rows, pending_rows = load_buy_signals(signals_path)
        if signal_rows is None:
            logger.warn("  buy_signals missing: %s" % signals_path)
        else:
            logger.info("  rows=%d pending=%d" % (len(signal_rows), len(pending_rows)))

        logger.info("step 5/7 计算计数、逐只重算与风险名单")
        report, report_path, expected_date = build_report(
            cfg, verify, review_text, signal_rows, pending_rows, logger
        )
        logger.info("  report chars=%d" % len(report))

        logger.info("step 6/7 写入报告: %s" % report_path)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report, encoding="utf-8")
        logger.info("  wrote=%d bytes" % report_path.stat().st_size)

        logger.info("step 7/7 完成")
        status = "OK"
        return 0
    except BuildError as exc:
        logger.error("BUILD STOPPED: %s" % exc)
        logger.error("No report was written for this run.")
        return 1
    except Exception:
        logger.error("UNEXPECTED ERROR:\n%s" % traceback.format_exc())
        logger.error("No report was written for this run.")
        return 1
    finally:
        logger.close(status)


if __name__ == "__main__":
    sys.exit(main())
