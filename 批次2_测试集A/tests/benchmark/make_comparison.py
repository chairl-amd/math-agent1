#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工单5：基于两轮全量 benchmark 生成 SymPy-only 与 Hybrid 的对比报告。

输入：两个 benchmark_result.json（同一套用例、同一版代码，只差 --verifier）。
输出：output/verifier_comparison.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
DEFAULT_OUT = BASE / "output" / "verifier_comparison.md"

# 有明确结论 = 校验器给出了 pass/fail，或明确判定“不是公式”。
CLEAR_VERDICTS = {"pass", "fail", "non_formula"}


def load_cases(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(case["id"]): case for case in data.get("cases") or []}


def format_rate(value) -> str:
    if value is None:
        return "-"
    return f"{value * 100:.2f}%"


def _elapsed_of(case: dict) -> float:
    """兼容 elapsed_sec 与 elapsed 两种字段名。"""
    value = case.get("elapsed_sec")
    if value is None:
        value = case.get("elapsed")
    return float(value or 0.0)


def summarize(cases: dict[str, dict]) -> dict:
    total = len(cases)
    clear = sum(1 for c in cases.values() if c.get("actual") in CLEAR_VERDICTS)
    hits = sum(1 for c in cases.values() if c.get("hit"))
    elapsed = [_elapsed_of(c) for c in cases.values()]
    unknown = sum(1 for c in cases.values() if c.get("actual") == "unknown")
    return {
        "total": total,
        "clear": clear,
        "clear_rate": (clear / total) if total else None,
        "hits": hits,
        "accuracy": (hits / total) if total else None,
        "unknown": unknown,
        "avg_elapsed": (sum(elapsed) / len(elapsed)) if elapsed else 0.0,
    }


def build_report(sympy_cases: dict[str, dict],
                 hybrid_cases: dict[str, dict],
                 sympy_path: str,
                 hybrid_path: str) -> str:
    common = [cid for cid in sympy_cases if cid in hybrid_cases]
    sympy_stats = summarize({cid: sympy_cases[cid] for cid in common})
    hybrid_stats = summarize({cid: hybrid_cases[cid] for cid in common})

    lines = [
        "# SymPy-only 与 Hybrid 校验对比",
        "",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- SymPy-only 数据：`{sympy_path}`",
        f"- Hybrid 数据：`{hybrid_path}`",
        f"- 对比用例数：{len(common)}",
        "",
        "## 汇总",
        "",
        "| 模式 | 明确结论数 | 明确结论率 | 命中数 | 准确率 | unknown 数 | 平均耗时(s) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, stats in (("sympy-only", sympy_stats), ("hybrid", hybrid_stats)):
        lines.append(
            f"| {label} | {stats['clear']} | {format_rate(stats['clear_rate'])} | "
            f"{stats['hits']} | {format_rate(stats['accuracy'])} | "
            f"{stats['unknown']} | {stats['avg_elapsed']:.2f} |"
        )

    lines += [
        "",
        "## 逐例明细",
        "",
        "| 用例 | 预期 | SymPy-only | Hybrid | 耗时差(s) |",
        "|---|---|---|---|---:|",
    ]
    for cid in common:
        sympy_case = sympy_cases[cid]
        hybrid_case = hybrid_cases[cid]
        delta = _elapsed_of(hybrid_case) - _elapsed_of(sympy_case)
        lines.append(
            f"| {cid} | {sympy_case.get('expected')} | "
            f"{sympy_case.get('actual')} | {hybrid_case.get('actual')} | "
            f"{delta:+.1f} |"
        )

    improved = [
        cid for cid in common
        if sympy_cases[cid].get("actual") not in CLEAR_VERDICTS
        and hybrid_cases[cid].get("actual") in CLEAR_VERDICTS
    ]
    degraded = [
        cid for cid in common
        if sympy_cases[cid].get("actual") in CLEAR_VERDICTS
        and hybrid_cases[cid].get("actual") not in CLEAR_VERDICTS
    ]
    both_unknown = [
        cid for cid in common
        if sympy_cases[cid].get("actual") not in CLEAR_VERDICTS
        and hybrid_cases[cid].get("actual") not in CLEAR_VERDICTS
    ]

    lines += [
        "",
        "## 结论",
        "",
        f"- Hybrid 把 {len(improved)} 个用例从「无法判定」推进到「明确结论」："
        + ("、".join(improved) if improved else "无"),
        f"- 反向退化（Hybrid 变模糊）的用例："
        + ("、".join(degraded) if degraded else "无"),
        f"- 两者都只能给出 unknown/incomplete 的用例："
        + ("、".join(both_unknown) if both_unknown else "无"),
        f"- 明确结论率：{format_rate(sympy_stats['clear_rate'])} → "
        f"{format_rate(hybrid_stats['clear_rate'])}；"
        f"准确率：{format_rate(sympy_stats['accuracy'])} → "
        f"{format_rate(hybrid_stats['accuracy'])}",
        f"- 平均耗时：{sympy_stats['avg_elapsed']:.2f}s → "
        f"{hybrid_stats['avg_elapsed']:.2f}s（Hybrid 增加异构模型调用）",
        "",
        "> 说明：Hybrid 的收益集中在「SymPy 解析失败或数值抽样不足以证明」的用例；"
        "代价是每个用例多一次异构模型往返。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="生成校验器对比报告")
    parser.add_argument("--sympy", required=True, help="sympy-only 的 benchmark_result.json")
    parser.add_argument("--hybrid", required=True, help="hybrid 的 benchmark_result.json")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    sympy_path = Path(args.sympy)
    hybrid_path = Path(args.hybrid)
    for path in (sympy_path, hybrid_path):
        if not path.is_file():
            print(f"缺少输入文件：{path}", file=sys.stderr)
            return 2

    report = build_report(
        load_cases(sympy_path), load_cases(hybrid_path),
        str(sympy_path), str(hybrid_path))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"对比报告已写入：{out_path}")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    raise SystemExit(main())
